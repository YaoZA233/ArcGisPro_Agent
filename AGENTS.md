# Repository guidance

- Target Windows + ArcGIS Pro 3.5.2 (Pro Python 3.11). Keep the core importable without ArcPy.
- Run offline tests with `./scripts/test.ps1`; they need only system Python >=3.11.
- `./scripts/agent.ps1 doctor` is read-only and checks the actual licensed runtime.
- Real ArcPy smoke testing: run `scripts/smoke_arcpy.py` with Pro Python. It writes synthetic data only under `workspace/smoke`; never use user datasets for fixture creation.
- Do not install packages into Esri's base Python environment. Use source imports or a cloned environment for future dependencies.
- Tool writes must be declared, explicitly enabled by the caller, placed under a unique run directory, and never overwrite existing datasets.
- Do not add arbitrary eval/exec or shell execution tools. Keep API credentials out of source and logs.
- Distinguish offline simulation, tool success and business-level GIS verification in user-facing reports.
- The toolbox can access CURRENT; subprocess workers cannot. Do not save or mutate an existing project implicitly.
- Update README and tool-contract tests when changing the public plan schema or CLI.
- Record every functional change or fix batch in CHANGELOG.md: date, concrete behavior changed, affected modules, verification, and known limitations. Do not include credentials or claim unperformed validation.
