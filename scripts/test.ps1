$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Join-Path $repoRoot 'src') + [IO.Path]::PathSeparator + $previousPythonPath
    & python -m unittest discover -s (Join-Path $repoRoot 'tests') -v
    $testExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $testExitCode
