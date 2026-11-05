# ArcGIS Pro Agent

[简体中文](README.md) | **English**

A local GIS agent for **ArcGIS Pro 3.5.2 on Windows**. Current version: `0.1.0`.

![license](https://img.shields.io/badge/license-MIT-green) ![python](https://img.shields.io/badge/python-3.11%20%7C%203.14-blue) ![ArcGIS%20Pro](https://img.shields.io/badge/ArcGIS%20Pro-3.5.2-orange)

Drive ArcGIS Pro in natural language to inspect data, run vector and raster analysis, and produce layouts. The model only plans; **every tool call, including read-only ones, is confirmed by you first and executed only after approval**. Results land in a task directory with a unique ID, so each run is auditable end to end. Two entry points — a command line and a docked pane inside ArcGIS Pro — share one agent, one connection configuration, one tool set and one task record.

- Providers: DeepSeek, Qwen, Kimi, GLM, OpenAI GPT, Anthropic Claude, xAI Grok, plus any OpenAI-compatible endpoint or relay. The original DeepSeek configuration keeps working.
- **60 structured tools** and **7 multi-step skill packs**, with fully typed argument validation.
- No third-party Python dependencies, and nothing is installed into ArcGIS's default environment; offline tests only need system Python ≥ 3.11.

| Section | Contents |
| --- | --- |
| [Quick start](#quick-start) | Three commands to the first conversation |
| [Using it inside ArcGIS Pro](#using-it-inside-arcgis-pro) | Pane install, approval cards, attachments, current map |
| [Providers and relays](#providers-and-relays) | Connection management, protocol scope |
| [Capabilities](#capabilities) | Tool groups and argument constraints |
| [Running real GIS work](#running-real-gis-work) | JSON plans and the task directory layout |
| [Configuration](#configuration) | Local overrides, interpreter discovery, system prompt |
| [Repository layout](#repository-layout) | What each directory is for |
| [Verification and tests](#verification-and-tests) | 229 offline tests, ArcPy smoke runs |
| [Security and privacy](#security-and-privacy) | Where keys live, what leaves the machine |
| [Current limits and roadmap](#current-limits-and-roadmap) | Known boundaries |

Every feature and fix batch is recorded with its concrete changes, affected modules, verification and remaining limitations in [CHANGELOG.md](CHANGELOG.md) (Chinese).

## Quick start

Open PowerShell in the repository root. Python **3.11 or newer** is required. There is nothing to install.

```powershell
# First use: paste your DeepSeek API key at the hidden prompt
.\scripts\agent.ps1 setup-key

# Start a conversation in the terminal; /new starts a fresh one, /quit exits
.\scripts\agent.ps1 chat

# Discover the ArcGIS Python automatically; check ArcPy, licensing and the real version
.\scripts\agent.ps1 doctor
```

Other entry points:

```powershell
.\scripts\agent.ps1 chat --allow-writes   # pre-enable write capability; each step still asks
.\scripts\agent.ps1 demo                  # offline simulation: exercises scheduling and logs, writes no GIS data
.\scripts\agent.ps1 tools                 # list tools and their arguments
.\scripts\agent.ps1 skills                # list multi-step skill packs
.\scripts\test.ps1                        # run the tests that do not need ArcGIS
```

Verified on the development machine through ArcPy: `E:\programs\ArcGIS\bin\Python\envs\arcgispro-py3\python.exe`, ArcGIS Pro **3.5.2**, Python **3.11.11**, license level **ArcInfo**. The registry version field may report the base version, so trust what `doctor` reads from ArcPy.

Keys are stored in the ignored `.env.local` at the repository root, as an ordinary unencrypted local file; a process environment variable for the matching provider takes precedence. Enter them through the hidden `setup-key` prompt or the panel's dedicated API key field — never in a chat message or a command-line argument. The legacy DeepSeek settings in `config/local.toml` still apply until you save a connection, after which the workspace connection configuration wins.

If the PowerShell execution policy blocks the scripts, run them from a shell that permits local scripts, or use Python directly:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m arcgis_agent demo
python -m unittest discover -s tests -v
```

`python -m pip install -e .` also works during development and gives you the `arcgis-agent` command; it is not a prerequisite for running the project.

## Using it inside ArcGIS Pro

1. Run `setup-key` first, then restart ArcGIS Pro. The panel can be opened and connections added even before a key is configured; see the end of this section to install or reinstall the add-in.
2. On the ribbon, open the **GIS Agent** tab and click **打开 Agent** ("Open Agent"; group **智能助手** — the button captions ship in Chinese). The pane docks and resizes. Type a task and click **发送** ("Send"; `Ctrl+Enter` also sends).
3. The pane streams model reasoning and a per-step tool timeline: one row per tool call showing "step N · tool name · state (waiting for approval → running → done / failed)" plus that step's duration, expandable to arguments and results. Worker logs scroll live while a step runs (ArcPy startup and tool phase markers), so a stall is no longer silent — 180s without progress at startup is reported and terminated. Each step raises a confirmation card with the action, arguments, impact and expected output; choose **允许本次读取 / 允许本次写入** (allow this read / write), **拒绝** (deny) or **取消任务** (cancel). Deny is the default focus, and a task stops if no answer arrives within 10 minutes. **新对话** ("New conversation") clears the current session context.

Example prompts: "list the layers on the current map", "buffer the roads by 500 m and add the result to the current map", "select features where land class is A, add an area field on a copy and compute square metres". The agent keeps planning from the tool results, and every step waits for its own confirmation. Input data can be dragged into the pane or given as full local paths. The **允许写入** ("allow writes") checkbox only pre-enables write capability; it does not skip later confirmations, and single writes can still be approved without it.

The pane offers model list refresh, model name, thinking level (off / low / high / max), output token cap and image fidelity settings. Reasoning content renders live as the API streams it (the "模型推理 · 实时生成" panel expands automatically and stays collapsed if you close it manually). The header reports real input, output and cache-hit token counts plus elapsed time for the task, accumulating across multiple model calls; `—` is shown when the API returns no usage.

Attachments can be added by button or drag-and-drop: images, UTF-8 text, and GIS data paths. Image and text content is sent to the active provider; `.shp`, `.gdb`, `.gpkg`, `.tif`, `.aprx`, `.lyrx` and `.xlsx` send only their local paths for tools to read. Up to 6 attachments per message, 6 MiB per image, 256 KiB and 40000 characters per text file, 16 MiB total. DeepSeek image input currently works with `deepseek-flash`; remove image attachments when switching to `deepseek-v4-pro`.

Supported: stopping the current task, resuming past conversations, exporting to Markdown, copying answers, opening the task directory, and adding generated data to the current map. Sessions live in `workspace/chats`; image bytes are not written into history files, so re-attach an image after resuming. Partial GIS results created before a stop may remain. The appearance button toggles light or dark, motion can be turned off in settings, and the system animation preference is respected.

The pane passes the current Pro project path into the conversation. After you approve `map_context`, the Pro SDK reads the session's map, layers and unsaved visibility state and reports supported local data paths. Approving `add_to_map` or `set_layer_visibility` modifies the specified map, and **the project is never saved automatically**. Analysis runs in a separate ArcPy process that reads data from disk: it cannot reach `CURRENT` and does not inherit the Pro selection set. A task that must cover only selected features needs an explicit attribute condition, or a selection export first (`get_selection` → `export_selection`); processing a whole layer must never be presented as processing the selection.

Building the pane requires a .NET 8 SDK and your local ArcGIS Pro installation directory (the project references the Pro SDK DLLs directly from `ArcGISProBin`, defaulting to `E:\programs\ArcGIS\bin`; override it if Pro is installed elsewhere):

```powershell
.\scripts\build-addin.ps1
.\scripts\build-addin.ps1 -ArcGISProBin 'D:\Program Files\ArcGIS\Pro\bin'
```

The output is `addin\ArcGISAgent\bin\Release\net8.0-windows\ArcGISAgent.esriAddinX`; double-click it to install. After an add-in update you must reinstall it to see the new pane.

The retained Python toolbox can be added separately under **Catalog → Toolboxes → Add Toolbox**, pointing at `arcgis/ArcGISAgent.pyt`. **Inspect current project** reads `CURRENT` inside the Pro process; **Execute agent plan** runs a JSON plan.

Toolbox mode allows `CURRENT` for the `project` parameter of `project_info`. The CLI and the chat pane's background process must use a `.aprx` path and cannot reach Pro's `CURRENT`.

## Providers and relays

In the Pro pane, expand **模型与运行设置 → 管理连接 / API 密钥** (model and run settings → manage connections / API keys), pick a provider preset, fill in the actual model ID and key, then click **保存并使用此连接** ("save and use this connection"). To use several accounts or relay addresses with the same vendor, choose **从预设新建独立连接** ("new connection from preset"). The **当前连接** selector at the top switches between them, **刷新** ("refresh") fetches the model list for your account, and model IDs can also be typed manually.

```powershell
.\scripts\agent.ps1 providers list
.\scripts\agent.ps1 providers presets
.\scripts\agent.ps1 setup-key --provider qwen
.\scripts\agent.ps1 providers use qwen
.\scripts\agent.ps1 chat
# In a conversation: /providers, /provider claude, /models, /model <actual model id>, /status
# Custom endpoint: edit the example's address, model and capabilities first
.\scripts\agent.ps1 providers save examples\provider-relay.json
.\scripts\agent.ps1 setup-key --provider my-relay
.\scripts\agent.ps1 providers use my-relay
# Remove a connection that is not active: providers delete <connection id>
```

Both the OpenAI Chat Completions-compatible protocol and the native Anthropic Messages protocol are supported. Image, tool and reasoning parameters are configured per model — vendor branding alone does not establish capability, and a preset model ID does not guarantee your account has access. Each connection keeps its own key locally and never borrows the DeepSeek one. Endpoint details, reasoning modes, storage and protocol scope are documented in [docs/providers.md](docs/providers.md) (Chinese).

## Capabilities

| Group | Tools |
| --- | --- |
| Inspect and read | `environment`、`project_info`、`describe`、`list_datasets`、`count`、`read_rows`、`verify_dataset` |
| Data ingestion | `import_table_points`、`convert_dataset` |
| Feature and raster analysis | `buffer`、`copy_features`、`clip`、`intersect`、`merge`、`dissolve`、`project`、`spatial_join`、`select_features`、`select_by_location`、`near_analysis`、`create_thiessen_polygons`、`clip_raster`、`union`、`erase`、`reshape_geometry`、`join_table`、`summarize_statistics` |
| Surface and raster analysis | `interpolate_surface`、`kernel_density`、`polygon_to_raster`、`terrain_derivatives`、`contour_lines`、`zonal_statistics`、`extract_by_mask`、`sample_raster_at_points`、`reclassify`、`hillshade`、`resample`、`project_raster`、`fill`、`flow_direction`、`flow_accumulation` |
| Copy editing and export | `add_field`、`set_field_value`、`delete_fields`、`calculate_geometry`、`repair_geometry`、`calculate_field`、`define_projection`、`export_table_csv`、`export_features` |
| Cartography and layout | `build_layout`、`export_layout` |
| Skills | `list_skills`、`read_skill` |
| Pro pane host | `map_context`、`get_selection`、`export_selection`、`add_to_map`、`set_layer_visibility` |

Coverage includes: data inspection, compound attribute filtering, selection by location, proximity analysis, Thiessen polygons, buffering, clip, intersect, merge, dissolve, projection, spatial join, raster clip, copy editing of attributes and geometry, constrained arithmetic field calculation, structured dataset acceptance checks, CSV/GeoJSON/Shapefile export, table and GPKG/KML ingestion, interpolation and density, zonal statistics, terrain and contours, reclassification and hillshade, raster resampling and projection, hydrology chains (fill / flow direction / flow accumulation), overlay and erase, geometry reshape, attribute joins, layout construction with image/PDF export, declarative skill-pack reading, plus reading the current Pro map, adding layers and toggling visibility.

Seven skill packs are built in — hydrology watershed, suitability site selection, data quality check, interpolation surface map, coordinate-unify overlay, CSV points to density map, and copy-edit delivery. They chain verified tools into multi-step method templates the model can load; see [docs/skills.md](docs/skills.md) (Chinese).

Arguments, filter syntax and restrictions are documented in [docs/tools.md](docs/tools.md); `agent.ps1 tools` returns the complete typed schema. Only the tools above are exposed — this is not the whole of ArcGIS.

Constraints that bite most often: `distance` accepts a positive number plus `Meters`, `Kilometers`, `Feet` or `Miles` (e.g. `500 Meters`), and `tolerance` in `reshape_geometry` uses the same linear form; `cell_size`, `interval`, `search_radius` and `z_factor` on analysis tools take bare numbers in the units of the input data, so a geographic coordinate system needs degree values (e.g. `0.0005`); `output_name` must start with a letter and contain only ASCII letters, digits or underscores, up to 64 characters, and the output directory is assigned by the system — arbitrary output paths are not accepted.

## Running real GIS work

Copy `examples/inspect_project.json` or `examples/buffer.json` and replace the sample data paths with your own **absolute paths**. Paths in a plan do not resolve relative to the JSON file.

```powershell
# Read a saved project
.\scripts\agent.ps1 run examples\inspect_project.json

# Read data, then create a 500 m geodesic buffer
.\scripts\agent.ps1 run examples\buffer.json --allow-writes

# Validate the plan and scheduling only, without calling ArcPy
.\scripts\agent.ps1 run examples\buffer.json --mock --allow-writes
```

The CLI prints `[等待本次授权]` ("waiting for this approval") at every step: `y` allows, `n` denies, `c` cancels, and pressing Enter alone denies. Neither `--allow-writes` nor `--mock` skips the per-step prompt. A denial stops later steps of that task while keeping everything already produced. The Python toolbox uses a Windows confirmation dialog with the same semantics; a programmatic call with no approval handler returns `approval_required` and executes nothing.

Each task automatically creates:

```text
workspace/runs/<UTC timestamp_unique ID>/
  events.jsonl           # tool requests, per-step approval decisions, execution, results and task-end events
  summary.json           # final status, arguments, return values, errors
  outputs/results.gdb/   # output geodatabase for real write tasks
  outputs/<name>.tif     # raster output (when raster tools are used)
```

Timestamps in logs are UTC. Offline simulation observations carry `simulated: true` and never fabricate a GDB. Errors, missing write permission and step-limit exhaustion return `failed`, `blocked` and `step_limit` respectively; denial, a missing approval handler and a pane approval timeout return `denied`, `approval_required` and `approval_expired`; cancellation returns `cancelled`. All of these unfinished states exit non-zero.

## Configuration

You can rely on automatic discovery, or copy `config/agent.example.toml` to `config/local.toml`:

```powershell
.\scripts\agent.ps1 --config config\local.toml doctor
.\scripts\agent.ps1 --config config\local.toml run examples\buffer.json --allow-writes
```

Relative paths inside a config file resolve against that file's directory. The CLI and the pane read the repository's `config/local.toml` automatically; `--config` points the CLI at another file. `config/local.toml` is excluded by `.gitignore` — change `config/agent.example.toml` when a default should be shared. With no config file, the CLI's output workspace defaults to `workspace` under the current directory; the pane starts background processes with the repository as the current directory; the toolbox defaults to this repository's `workspace` and picks up `config/local.toml` when present.

The system prompt sent to the model is the bundled [src/arcgis_agent/prompts/system.md](src/arcgis_agent/prompts/system.md) — the single source of truth for approval rules, tool usage, acceptance criteria and reporting. Edit that file to change model behaviour; contract tests guard the key rule markers, and heavy expansions raise the token cost of every request.

`ARCGIS_PRO_PYTHON` can point at a `python.exe` from an ArcGIS Pro cloned environment and takes precedence over configuration. Registry discovery only selects the default environment, so specify clones explicitly. `doctor` checks the exact version against `expected_version`; `run` performs no extra version probe.

## Repository layout

```text
.
├── src/arcgis_agent/         # agent core (pure Python, no third-party dependencies)
│   ├── agent.py              # plan-execute loop and step ceiling
│   ├── catalog.py            # schemas, argument validation and ArcPy implementations for 60 tools
│   ├── providers.py          # provider presets, connection storage and key read/write
│   ├── deepseek.py           # streaming client for the OpenAI-compatible protocol
│   ├── interfaces.py         # CLI / chat / providers / doctor command entry points
│   ├── approvals.py          # per-step approval handlers
│   ├── runtime.py            # ArcPy child-process worker, timeout and stall watchdog
│   ├── chat.py               # sessions, history and usage accounting
│   ├── cartography.py        # layout construction and image/PDF export
│   ├── selections.py         # Pro selection snapshots and selection_token
│   ├── skills.py             # declarative skill-pack loading and validation
│   ├── prompts/system.md     # system prompt (single source of truth)
│   └── skills/*.json         # 7 built-in skill packs
├── addin/ArcGISAgent/        # ArcGIS Pro docked pane (C# / WPF, .NET 8)
├── arcgis/                   # retained Python toolbox (*.pyt plus tool documentation)
├── scripts/                  # agent.ps1, test.ps1, build-addin.ps1, smoke_*.py
│   └── debug/                # one-off diagnostic scripts, see scripts/debug/README.md
├── tests/                    # offline contract tests, no ArcGIS required
├── examples/                 # JSON plan examples
├── config/                   # agent.example.toml template (local.toml is not committed)
├── docs/                     # architecture / tools / providers / skills / validation (Chinese)
│   └── notes/                # historical debugging notes (process record, not usage docs)
└── workspace/                # runtime output, fully ignored (task records, sessions, connections, smoke data)
```

## Verification and tests

229 offline tests cover typed tool arguments, per-step approval, single-write permission, denial / cancellation / dropped connections, approval response replay, bounded loops, task recording, worker protocol faults and recovery, multi-model protocols and the subprocess protocol, layout workflows, extent units and coordinate-system consistency, read-only export, skill-pack validation, tool event step numbering, and worker progress streaming with the startup stall watchdog. Both system Python 3.14 and Pro Python 3.11 pass; [CI](.github/workflows/tests.yml) uses the same version matrix and never depends on ArcGIS in the cloud.

```powershell
.\scripts\test.ps1
```

Local integration check against real ArcPy (run with Pro's own Python):

```powershell
& 'E:\programs\ArcGIS\bin\Python\envs\arcgispro-py3\python.exe' scripts\smoke_arcpy.py
```

It creates synthetic features and rasters under `workspace/smoke/<unique ID>`, then runs buffer regression, data ingestion and a fake-geodatabase guard through a separate ArcPy subprocess, together with the full batch-2 analysis set (interpolation, density, polygon-to-raster, zonal statistics, extract by mask, raster sampling, terrain, contours, statistics, attribute join, overlay, erase, geometry reshape), and verifies that denials behave and source data stays untouched. Inputs and outputs are kept for review; none of your own data is used.

`scripts/smoke_gis_tools.py` validates 20 real ArcPy tools in an isolated synthetic directory, checking that originals are unmodified and overwrite is refused. `scripts/smoke_agent_tools.py <synthetic data directory>` drives the full "inspect → filter → buffer → count" flow with a configured model and does incur API usage. The approval callbacks in these fixture scripts only permit operations inside their own synthetic data root; the product entry points still hand every step to the user.

Cartography has `scripts/smoke_cartography.py`, also run with Pro Python. It builds synthetic polygons, projected polygons, a raster and an empty table under `workspace/smoke/cartography_<unique ID>`, calls `build_layout` and `export_layout` through a separate ArcPy subprocess, and checks the produced `.aprx`, the PNG/JPEG/PDF/TIFF/SVG outputs, unchanged project checksums before and after export (read-only export), the layout inventory returned by `project_info`, the **non-white pixel ratio** of each export (proving the page actually rendered data rather than a blank sheet), plus the refusal paths — overwrite, layer without geometry, mismatched coordinate system, metric extent on a geographic coordinate system, unknown layout, missing project — leaving no residue behind. It never opens or modifies your existing projects.

`scripts/smoke_toolbox.py` verifies toolbox discovery only by default and returns `executed: false`; add `--interactive` to run a plan with per-step confirmation dialogs. `scripts/smoke_chat.py` requires terminal confirmation for its real tool steps. Detailed results and unverified items are listed in [docs/validation.md](docs/validation.md) (Chinese).

Inside a restricted execution sandbox ArcPy can crash at the Windows native level (this machine returned `3221225477` once), while the same command succeeds in a normal local run. Such an error is never treated as success, and it is distinct from exceptions raised by Python tool code.

## Security and privacy

Full text in [SECURITY.md](SECURITY.md). In short:

- API keys are written only to `.env.local` in the repository root; `workspace/` stores connection profiles (endpoint, model ID, capability flags) without any key. Both are excluded by `.gitignore`, and no real credential appears anywhere in the source.
- Enter keys through the hidden `setup-key` prompt or the pane's key field. Never pass them as command-line arguments (they end up in the Windows process list and shell history), and never paste them into a conversation or an issue.
- Conversation content, attached text and the tool results you approve are sent to the active provider; GIS data files themselves are not uploaded — only their local paths.
- Task logs contain input paths, field information and approval decisions, so manage `workspace` according to your local GIS data handling requirements.
- This repository collects no telemetry and executes no remote code; apart from model API calls it makes no external network requests.

## Current limits and roadmap

- Available today: multi-provider streaming chat, connection configuration, attachments, usage accounting, session history, CLI, the Pro pane, 60 structured tools, 7 declarative skill packs, per-call user approval, the current-map and selection host bridge, layout cartography with image/PDF export, and task logging.
- Next: more analysis tools, map styles and multi-frame cartography, business-level output quality checks.
- Surface and raster analysis needs the Spatial Analyst extension. If the license check-out fails before execution, the whole call fails and reports the license state — no partial results.

Each CLI call starts its own ArcPy process: reliable, but with import overhead. A CLI timeout terminates the worker — if the worker had already written a complete result, it is returned and marked `worker_terminated`; otherwise the error keeps the tail of the worker's stderr plus a recovery hint. Partial results created earlier may remain, and there is no transactional rollback today. The toolbox inside Pro executes synchronously in the current process without a forced timeout, so long tasks need Pro's own task management.

Data editing tools copy first and edit the copy; overwrite is closed off, and there is no delete-dataset or arbitrary Python execution tool. One exception on the project side: `build_layout` copies Pro's own blank template and creates and saves a standalone layout project inside your current task at `workspace/runs/<task ID>/outputs/cartography/`. It never opens, saves or alters your existing projects, and `export_layout` opens that layout project read-only (verified by checksums in the real smoke run). Adding layers and setting visibility do modify the current map, and each such call needs explicit approval.

## Contributing

Read [AGENTS.md](AGENTS.md) first for the repository constraints (the core stays importable without ArcPy, writes must be declared and approved per call, nothing is installed into Esri's base environment, and public plan-schema or CLI changes must update the README and the tool-contract tests). Run `.\scripts\test.ps1` before committing, and record each feature or fix batch in [CHANGELOG.md](CHANGELOG.md).

Architecture notes are in [docs/architecture.md](docs/architecture.md) and source references in [docs/reference.md](docs/reference.md). The documentation set is currently Chinese.

## License

[MIT](LICENSE) © 2026 YaoZA233
