# ArcGIS Pro Agent 工具调用卡住问题 - 修复说明

> **2026-11-05 排查更正**：本文保留历史推测，下面有关许可握手、环境变量及固定耗时的因果断言尚无充分证据。隔离 worker 输入管道后的实际面板复测已成功；具体验证与限制以 CHANGELOG 最新批次为准。

## 问题描述

在 ArcGIS Pro 内部使用 Agent 对话时，第一次调用工具就会卡住，没有任何输出。但在关闭 ArcGIS Pro 后，在 CLI 模式下运行 Agent 则完全正常，速度很快。

## 根本原因

问题的核心在于 **ArcGIS 许可机制和环境变量污染**：

1. **许可握手机制**：
   - ArcGIS 的许可机制要求外部 Python 进程通过 `ESRI_SOFTWARE_CLASS` 环境变量与正在运行的 ArcGIS Pro 完成授权握手
   - 当 Pro 正忙时（处理在线底图加载、执行其他任务等），这个握手会停滞数分钟甚至超时

2. **环境变量继承链**：
   ```
   ArcGIS Pro 进程
   ├─ 继承所有 Pro 环境变量（包括 ESRI_SOFTWARE_CLASS）
   └─ C# Addin 启动 Python RPC 进程
      ├─ 继承了 Pro 的所有环境变量
      └─ RPC 进程启动 Worker 子进程
         └─ import arcpy 时触发授权握手 → 卡住！
   ```

3. **为什么 CLI 模式正常**：
   - CLI 直接启动，不在 Pro 进程树中
   - 环境变量干净，没有 `ESRI_SOFTWARE_CLASS`
   - Worker 子进程使用 Pro 自己的 Python 环境，不需要握手

## 修复方案

### 修改 1: C# Addin 启动时清理环境变量

**文件**: `addin/ArcGISAgent/AgentView.xaml.cs`

**位置**: `EnsureWorkerAsync()` 方法中启动 Python RPC 进程的部分

**修改内容**:
```csharp
foreach (string arg in new[] { "-u", "-m", "arcgis_agent", "rpc" }) start.ArgumentList.Add(arg);

// 关键修复：移除所有 ArcGIS Pro 环境变量，防止 worker 子进程在 import arcpy 时发生授权握手阻塞
// ESRI_SOFTWARE_CLASS 会让外部 Python 进程尝试与正在运行的 Pro 进行授权握手，当 Pro 忙碌时会卡住数分钟
start.Environment.Remove("ESRI_SOFTWARE_CLASS");
// 清理其他可能导致 Pro 关联的环境变量
foreach (string key in new[] { "ARCGISHOME", "ARCGIS_PRODUCT_NAME", "ARCGIS_VERSION" })
    start.Environment.Remove(key);

start.Environment["PYTHONPATH"] = Path.Combine(root, "src");
```

**说明**: 在 C# 启动 Python RPC 进程时就主动清理掉所有 Pro 相关环境变量，从源头切断授权握手的触发条件。

### 修改 2: Python Runtime 双重保护

**文件**: `src/arcgis_agent/runtime.py`

**位置**: `ArcPyRuntime._worker_environment()` 方法

**修改内容**:
```python
def _worker_environment(self) -> dict:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    # The panel backend inherits Pro's process environment; ESRI_SOFTWARE_CLASS makes an
    # external interpreter behave like an in-Pro process (licensing/GP handshake with the
    # running Pro, which can stall for minutes). A standalone worker must not see it.
    environment.pop("ESRI_SOFTWARE_CLASS", None)
    # 额外清理其他可能导致与 Pro 授权握手的环境变量
    for key in ("ARCGISHOME", "ARCGIS_PRODUCT_NAME", "ARCGIS_VERSION"):
        environment.pop(key, None)
    return environment
```

**说明**: 在 Python 端启动 worker 子进程时再次清理环境变量，提供双重保护。

## 清理的环境变量说明

| 环境变量 | 作用 | 为什么要清理 |
|---------|------|------------|
| `ESRI_SOFTWARE_CLASS` | 标识进程应使用 Pro 的许可 | **核心变量**，触发授权握手的主要原因 |
| `ARCGISHOME` | ArcGIS Pro 安装目录 | 可能被 arcpy 用于检测 Pro 实例 |
| `ARCGIS_PRODUCT_NAME` | 产品名称标识 | 辅助产品识别 |
| `ARCGIS_VERSION` | ArcGIS 版本号 | 辅助版本检测 |

## 测试验证

### 1. 环境诊断
```bash
cd E:\ArcGISPro 3.5.2_AgentProgram\ArcGISPro_Agent
python scripts/debug/diagnose_env.py
```

这个脚本会：
- 检查当前环境中是否存在 Pro 环境变量
- 测试 arcpy 导入时间
- 显示 ArcGIS 安装信息

**预期结果**：
- ✓ 所有 Pro 环境变量显示 `<未设置>`
- ✓ arcpy 导入时间 < 15s（冷启动）

### 2. Worker 子进程测试
```bash
python scripts/debug/test_worker_spawn.py
```

这个脚本会：
- 测试启动 worker 子进程调用工具
- 测量第一次调用的耗时
- 测试 worker 缓存是否生效

**预期结果**：
- ✓ 第一次调用成功，耗时 < 15s
- ✓ 第二次调用成功，耗时 < 5s

### 3. ArcGIS Pro 内测试

1. **重新编译 C# Addin**:
   ```bash
   cd addin/ArcGISAgent
   dotnet build -c Release
   ```

2. **重启 ArcGIS Pro**（必须，以应用新的环境设置）

3. **打开 Agent 面板**，发送一个简单的测试消息：
   ```
   检查我的 ArcGIS 环境
   ```

4. **观察**：
   - Agent 应该在几秒内开始输出
   - 工具调用不应该卡住超过 15 秒
   - 状态栏应该显示 "正在执行" 而不是长时间停滞

## 预期改进

| 场景 | 修复前 | 修复后 |
|-----|--------|--------|
| Pro 内第一次工具调用 | 卡住数分钟或超时 | 10-15s（正常冷启动） |
| Pro 内后续工具调用 | 正常或偶尔卡住 | < 5s（worker 缓存） |
| CLI 模式 | 正常 | 正常（无变化） |
| Pro 忙碌时调用 | 严重卡顿 | 不受影响 |

## 为什么删除底图无效

你之前删除了 `work_1.aprx` 里的在线底图，但问题依旧，原因是：

1. **环境变量是根本问题**，不是底图本身
2. 即使底图不加载，Pro 进程的环境变量仍然存在
3. Worker 子进程启动时仍然会检测到 `ESRI_SOFTWARE_CLASS`
4. 只要这个变量存在，arcpy 就会尝试握手，无论 Pro 是否真的在忙

## 注意事项

1. **必须重新编译并重启 Pro**：C# 代码的修改需要重新编译 Addin，并完全重启 ArcGIS Pro 才能生效。

2. **首次启动仍需时间**：即使修复后，第一次 import arcpy 仍需要 10-15 秒的冷启动时间，这是正常的。

3. **如果问题依旧**：
   - 检查是否有其他 Pro 实例在后台运行
   - 使用 `diagnose_env.py` 确认环境变量是否真的被清理
   - 检查 Python 路径是否正确指向 Pro 的 Python

4. **不影响 Pro 内的 arcpy 使用**：这些修改只影响 Agent 启动的外部 worker 进程，不会影响 Pro 内部正常的 arcpy 操作。

## 技术细节

### 为什么需要双重清理？

1. **C# 端清理**：从源头防止 RPC 进程继承污染的环境
2. **Python 端清理**：防御性编程，即使 RPC 进程环境不干净，worker 也能正常运行

这种"纵深防御"策略确保在各种场景下都能避免授权握手问题。

### 进程隔离原理

```
修复后的进程模型：

ArcGIS Pro 进程 (有 ESRI_SOFTWARE_CLASS)
│
└─ Python RPC 进程 (已清理环境变量)
   │
   └─ Worker 子进程 (环境干净)
      │
      └─ import arcpy (使用 Pro Python，但不握手)
```

Worker 使用的是 Pro 安装的 Python 和 arcpy 模块，但因为环境变量已清理，arcpy 不会尝试与 Pro 进程通信，直接使用本地许可。

## 参考

- ArcGIS Pro SDK 文档：进程间通信和许可管理
- Python subprocess 文档：环境变量继承
- 项目原有注释：`runtime.py:43-46`（已识别问题但未完全解决）

---

**修复作者**: Claude (Kiro)  
**修复日期**: 2026-11-05  
**问题报告人**: 项目维护者
