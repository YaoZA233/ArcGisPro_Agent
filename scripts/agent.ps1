# Run directly from source; no pip install into the ArcGIS environment is needed.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $previousPythonPath
    & python -m arcgis_agent @args
    $agentExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $agentExitCode
