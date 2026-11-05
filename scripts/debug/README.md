# 一次性诊断脚本

这些脚本用于排查 Pro 面板内「第一次调用工具就卡住」的问题，属于调试辅助，**不是产品入口，也不是自动化测试**。

- 常规回归测试：`.\scripts\test.ps1`（离线契约测试，真实测试用例在 `tests/`）。
- 真实 ArcPy 集成冒烟：`.\scripts\smoke_arcpy.py`、`smoke_cartography.py` 等。
- 运行时自检：`.\scripts\agent.ps1 doctor`。

## 脚本清单

| 脚本 | 用途 |
| --- | --- |
| `check_env.py` | 打印当前进程的环境变量，重点看 `ESRI_SOFTWARE_CLASS` 等 ArcGIS 相关项 |
| `check_actual_python.py` | 打印实际被解析到的 `python.exe`，用于确认是否落到了 Windows Store 占位符 |
| `check_pro_running.py` | 检测 ArcGIS Pro 是否在运行 |
| `diagnose_env.py` | 综合环境诊断：Python 路径、注册表发现、环境变量继承 |
| `diagnose_simple.py` | `diagnose_env.py` 的最小化版本，输出更集中 |
| `quick_test.py` | 对比不同环境变量组合下 `import arcpy` 的耗时 |
| `test_arcpy_import.py` | 单独验证 ArcPy 能否导入以及耗时 |
| `test_python_startup.py` | 验证候选解释器的启动开销 |
| `test_worker_spawn.py` | 验证 RPC 进程派生 worker 子进程的过程 |
| `verify_rpc_env.py` | 核对 RPC 子进程实际拿到的环境变量集合 |

从仓库根目录运行，例如：

```powershell
python scripts\debug\diagnose_env.py
python scripts\debug\test_worker_spawn.py
```

排查结论与限制见 [docs/fix-tool-hang-issue.md](../../docs/fix-tool-hang-issue.md) 和 [CHANGELOG.md](../../CHANGELOG.md)。
