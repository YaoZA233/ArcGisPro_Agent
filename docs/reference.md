# 参考项目记录

用户提供的本地参考工程：

`E:\ArcGISPro 3.5.2_AgentProgram\otherlib\GIS-agent-main`

本轮查看了其 README、pyproject.toml、`src/gis_cli/arcpy_bridge.py`、`src/gis_cli/engine/tools.py` 和目录结构。采用的架构思路包括：

- Agent 调度与 ArcPy 运行环境分离。
- 通过 Windows 注册表定位 ArcGIS Pro Python。
- 工具调用使用结构化结果，任务过程可追踪。
- 没有 ArcGIS 的环境也能进行调度测试。
- 用轮次上限控制执行预算。

本仓库为重新实现的最小基础工程，没有整体复制参考工程的源码、技能、图片或依赖。参考目录没有发现项目根级 LICENSE；子技能中的许可证不等同于整个项目的许可证。未来若要直接引入上游代码，应先核实其来源、版本与许可，再保留相应声明。

本轮没有确认上游 GitHub URL 和提交哈希，因此不将本仓库描述为该项目的官方分支或兼容实现。

## 工具与授权扩展

本轮再次参考 `GIS-agent-main/src/gis_cli/engine/tools.py` 的工具注册表与结构化观察结果组织方式，独立实现类型化工具目录、ArcPy 允许列表和逐次授权。未引入其任意 Python 执行入口。用户要求的每次工具确认由本项目统一 Agent 层执行，CLI、WPF 面板和 Python 工具箱共享该约束，不依赖模型自觉遵守提示。

## 多模型适配参考

按用户要求查看 `E:\ArcGISPro 3.5.2_AgentProgram\otherlib\rikkahub-master` 的 `ProviderSetting.kt`、`DefaultProviders.kt`、`ClaudeProvider.kt`、`ClaudeStreamDecoder.kt` 等文件。参考供应商与协议分离、独立连接、模型能力配置、原生内容块和用量转换的设计。

参考仓库根许可证为 **GNU AGPL v3**。本项目没有引入其 Kotlin 源码、UI 资产或依赖，Python/WPF 适配为独立实现。今后若复制或分发上游代码，应重新核实并遵守对应许可。本轮未核实远程提交版本，不将本实现描述为其分支或官方兼容实现。
