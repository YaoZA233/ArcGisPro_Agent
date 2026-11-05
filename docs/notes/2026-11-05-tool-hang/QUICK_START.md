# 修复总结和操作指南

## 🎯 问题根源

你遇到的**工具调用卡住**问题是由 **ArcGIS 许可授权握手机制** 引起的：

- ArcGIS Pro 面板启动的 Python RPC 进程**继承了 Pro 的环境变量**（特别是 `ESRI_SOFTWARE_CLASS`）
- Worker 子进程在 `import arcpy` 时检测到这个变量，尝试与正在运行的 Pro 进行授权握手
- 当 Pro 忙碌时（处理在线底图、地图渲染等），握手会**停滞数分钟甚至超时**
- CLI 模式正常是因为它独立运行，环境干净，不需要握手

## ✅ 已完成的修复

### 1. C# Addin 修复 (`addin/ArcGISAgent/AgentView.xaml.cs`)
在启动 Python RPC 进程时清理 Pro 环境变量：
```csharp
start.Environment.Remove("ESRI_SOFTWARE_CLASS");
foreach (string key in new[] { "ARCGISHOME", "ARCGIS_PRODUCT_NAME", "ARCGIS_VERSION" })
    start.Environment.Remove(key);
```

### 2. Python Runtime 修复 (`src/arcgis_agent/runtime.py`)
在启动 worker 子进程时再次清理（双重保护）：
```python
for key in ("ARCGISHOME", "ARCGIS_PRODUCT_NAME", "ARCGIS_VERSION"):
    environment.pop(key, None)
```

### 3. 诊断和测试工具
- `scripts/debug/diagnose_env.py` - 环境诊断
- `scripts/debug/test_worker_spawn.py` - Worker 启动测试
- `scripts/debug/quick_test.py` - 环境变量影响对比测试
- `docs/fix-tool-hang-issue.md` - 详细修复文档

## 📋 立即执行的操作步骤

### 步骤 1: 重新编译 C# Addin

**方法 1: 使用编译脚本（推荐）**

双击运行：
```
E:\ArcGISPro 3.5.2_AgentProgram\ArcGISPro_Agent\build.bat
```

**方法 2: 手动编译**

打开命令行，执行：

```bash
cd E:\ArcGISPro 3.5.2_AgentProgram\ArcGISPro_Agent
.\.dotnet\dotnet.exe build addin\ArcGISAgent\ArcGISAgent.csproj -c Release
```

**预期输出**：
```
已成功生成。
    0 个警告
    0 个错误
```

**注意**：必须使用项目本地的 `.dotnet\dotnet.exe`，不能用系统的 `dotnet` 命令。

### 步骤 2: 完全重启 ArcGIS Pro

**重要**：必须完全关闭并重新启动 ArcGIS Pro，否则修改不会生效。

1. 关闭所有 ArcGIS Pro 窗口
2. 检查任务管理器，确保没有 `ArcGISPro.exe` 进程残留
3. 重新启动 ArcGIS Pro

### 步骤 3: 测试修复效果

#### 3.1 在 Pro 外测试（可选，验证修复逻辑）

关闭 Pro，在命令行运行：

```bash
cd E:\ArcGISPro 3.5.2_AgentProgram\ArcGISPro_Agent

# 环境诊断
python scripts/debug/diagnose_env.py

# Worker 启动测试
python scripts/debug/test_worker_spawn.py

# 环境变量影响对比
python scripts/debug/quick_test.py
```

**预期结果**：
- ✓ 环境变量显示 `<未设置>`
- ✓ arcpy 导入时间 < 15s
- ✓ 工具调用成功

#### 3.2 在 Pro 内测试（关键）

1. **启动 ArcGIS Pro**
2. **打开 Agent 面板**（通过工具栏或菜单）
3. **发送测试消息**：
   ```
   检查我的 ArcGIS 环境
   ```

4. **观察行为**：
   - ✓ 应该在 **10-15 秒内**看到输出（首次冷启动）
   - ✓ 状态栏显示 "正在执行" → "工具执行中" → "任务完成"
   - ✓ 不应该长时间停滞或超时

5. **发送第二条消息测试缓存**：
   ```
   当前工程有哪些地图和图层？
   ```
   
   - ✓ 应该在 **5 秒内**响应（worker 缓存生效）

## 🔍 如何判断修复是否成功

### ✅ 成功的标志

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 首次工具调用 | 卡住 3+ 分钟或超时 | 10-15 秒正常响应 |
| 后续工具调用 | 可能卡住 | < 5 秒 |
| Pro 忙碌时 | 严重卡顿 | 不受影响 |
| 状态栏更新 | 长时间无反应 | 实时显示进度 |

### ❌ 如果问题依旧

1. **检查编译**：
   ```bash
   cd addin/ArcGISAgent
   dotnet build -c Release
   # 查看是否有编译错误
   ```

2. **确认 Pro 已重启**：
   - 检查任务管理器，确保没有旧的 Pro 进程
   - 重新加载 Addin

3. **运行诊断**：
   ```bash
   # 在 Pro 内打开 Python 控制台，运行：
   import os
   print("ESRI_SOFTWARE_CLASS:", os.environ.get("ESRI_SOFTWARE_CLASS"))
   print("ARCGISHOME:", os.environ.get("ARCGISHOME"))
   ```
   
   如果这些变量仍然存在，说明环境清理没有生效。

4. **查看详细日志**：
   - 检查 `workspace/runs/` 下的最新运行目录
   - 查看 `events.jsonl` 了解卡在哪个阶段

## 📊 性能基准参考

正常情况下的性能指标：

| 操作 | 预期时间 | 说明 |
|------|----------|------|
| 首次 import arcpy | 10-15s | 冷启动，加载 arcpy 模块 |
| 第二次 import arcpy | < 1s | Python 模块缓存 |
| 首次工具调用（environment） | 10-15s | 包含 worker 启动 + arcpy 导入 |
| 后续工具调用 | 3-5s | Worker 进程复用 |
| 复杂 GIS 操作（buffer） | 5-20s | 取决于数据量 |

## 🛠️ 故障排查

### 场景 1: 编译失败

**症状**：`dotnet build` 报错

**解决**：
```bash
# 检查 .NET SDK
dotnet --version  # 应该是 8.0 或更高

# 清理并重新编译
dotnet clean
dotnet restore
dotnet build -c Release
```

### 场景 2: 修复后仍然卡住

**症状**：重启 Pro 后问题依旧

**排查**：
1. 确认 C# 代码修改已保存
2. 确认编译成功且没有警告
3. 检查 Addin 是否真的重新加载（查看 Pro 的 Add-In Manager）
4. 尝试运行 `scripts/debug/diagnose_env.py` 确认环境变量

### 场景 3: Pro 崩溃或无法启动

**症状**：修改后 Pro 启动异常

**恢复**：
1. 检查编译错误日志
2. 回滚修改并重新编译
3. 检查 Pro 的事件查看器日志

## 📝 额外说明

### 为什么删除底图无效？

你之前删除在线底图后问题依旧，是因为：
- **环境变量是根本问题**，不是 Pro 是否真的在忙
- 只要 `ESRI_SOFTWARE_CLASS` 存在，arcpy 就会尝试握手
- 底图只是让握手卡得更久，但不是触发原因

### 为什么需要双重清理？

1. **C# 端清理**：从源头防止 RPC 进程继承污染
2. **Python 端清理**：防御性编程，即使漏了也能补救

这是"纵深防御"策略，确保在各种场景下都能避免问题。

### 这会影响 Pro 内的其他功能吗？

**不会**。修改只影响 Agent 启动的外部 worker 进程，不会影响：
- Pro 内部的 arcpy 操作
- Pro 的地图渲染和工具
- 其他 Python 工具箱
- Pro 的许可管理

## 📞 需要帮助？

如果按照以上步骤操作后问题仍未解决，请提供：

1. 编译输出（`dotnet build` 的完整输出）
2. 诊断脚本输出（`diagnose_env.py`）
3. Agent 面板的错误信息
4. 最新运行的 `events.jsonl` 文件内容

---

**预计修复时间**: 5-10 分钟（编译 + 重启 Pro）  
**预期效果**: 首次工具调用从超时/卡顿 → 10-15 秒正常响应  
**风险评估**: 低（仅清理环境变量，不影响核心功能）
