# 技能包（Skills）

技能包是**声明式多步方法模板**：把已验证的工具按正确顺序、正确参数编排成 JSON 数据包，供模型在复杂任务前加载参考。技能不是执行器——`read_skill` 只返回模板文本，模型仍须把占位符替换为真实路径、参数和上一步工具返回的输出，再逐个发起普通工具调用，**每一步照常单独询问用户授权**。系统不含任意代码执行：技能文件里没有 Python、没有 shell，只有工具名和参数模板。

## 内置技能

运行 `./scripts/agent.ps1 skills` 查看清单。当前内置 7 个：

| 技能 | 步骤链 | 许可 |
| --- | --- | --- |
| `hydrology_watershed` | describe → fill → flow_direction → flow_accumulation | Spatial Analyst |
| `suitability_site_selection` | describe → select_features → buffer → erase → verify_dataset | erase 需 Advanced |
| `data_quality_check` | describe → read_rows → verify_dataset → summarize_statistics | 核心 |
| `interpolation_surface_map` | describe → interpolate_surface → contour_lines → hillshade → build_layout → export_layout | Spatial Analyst |
| `coordinate_unify_overlay` | describe ×2 → project → intersect → verify_dataset | 核心 |
| `csv_points_to_density_map` | import_table_points → describe → kernel_density → build_layout → export_layout | Spatial Analyst |
| `attribute_edit_delivery` | describe → add_field → calculate_field → verify_dataset → export_table_csv | 核心 |

## 占位符

步骤参数里只允许两种占位符，加载时会严格校验：

- `{{params.<名称>}}`：引用技能声明的参数，由模型从用户请求中取值（路径、字段名、距离等）。
- `{{steps.<output_name>.<字段>}}`：引用**前面步骤**真实返回结果中的字段，例如 `{{steps.filled.output}}` 表示 fill 步骤输出栅格的路径，`{{steps.surface_map.project}}` 表示 build_layout 返回的工程路径。只能引用之前已出现的 `output_name`，前向引用会被拒绝。

模型执行时不得保留占位符：所有 `{{…}}` 必须替换为真实值后才发起工具调用。

## 加载时校验

每个技能文件在加载时对照当前工具注册表校验，任何漂移立即报错（fail fast），坏文件不会静默生效：

- 步骤 `tool` 必须存在于工具目录，且不能是 Pro 面板宿主工具；
- 参数键必须属于该工具 schema，必填参数不得缺失；
- 字面量字符串须符合该属性的 `enum` 与 `pattern`（如 `output_name` 命名规则、`format: PNG`）；含占位符的值跳过字面量检查；
- 占位符语法、参数声明和前向引用如上节校验；
- 顶层键白名单：`name/title/summary/when_to_use/requires/params/steps/verification/pitfalls`；
- `name` 为 `[a-z][a-z0-9_]{1,63}`，最多 12 个参数、12 个步骤、8 条注意事项。

## 用户自定义技能

在 `config/local.toml` 配置：

```toml
[agent]
skills_dir = "my_skills"   # 相对配置文件目录，也可用绝对路径
```

该目录下每个 `*.json` 是一个技能包，格式与内置一致。用户技能**按同名覆盖**内置技能（清单中 `source` 显示 `user`/`builtin`），目录内重名或文件损坏会在加载时报错并指明文件路径。用户技能同样经过全部校验，不能引入新工具或绕过授权。

## 边界

- `list_skills` / `read_skill` 是只读工具，在 CLI、聊天面板和工具箱中都可用，不启动 ArcPy 子进程；每次调用仍计入逐次授权。三个入口都从配置读取 `skills_dir`，未配置时退回内置技能。
- 技能模板只是方法参考：工具成功不等于业务正确，坐标单位、许可级别（如 erase 的 Advanced、Spatial Analyst 签出）和参数取值仍需按 `pitfalls` 与工具说明核对。
- 静态 JSON 计划（`agent.ps1 run`）不解析占位符；需要动态串联时使用聊天入口让模型按技能逐步执行。
