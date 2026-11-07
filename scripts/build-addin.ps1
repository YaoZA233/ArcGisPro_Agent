param([string]$ArcGISProBin = 'E:\programs\ArcGIS\bin')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$versionFile = Join-Path $repoRoot 'VERSION'
if (-not (Test-Path -LiteralPath $versionFile)) { throw "缺少版本文件：$versionFile" }
$agentVersion = (Get-Content -LiteralPath $versionFile -Raw).Trim()
if ($agentVersion -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION 必须是 major.minor.patch：$agentVersion" }
$project = Join-Path $repoRoot 'addin\ArcGISAgent\ArcGISAgent.csproj'
$localDotnet = Join-Path $repoRoot '.dotnet\dotnet.exe'
$dotnet = if (Test-Path -LiteralPath $localDotnet) { $localDotnet } else { 'dotnet' }
& $dotnet build $project -c Release "-p:ArcGISProBin=$ArcGISProBin" "-p:AgentVersion=$agentVersion" --nologo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$out = Join-Path $repoRoot 'addin\ArcGISAgent\bin\Release\net8.0-windows'
$stage = Join-Path $out ('package-' + [Guid]::NewGuid().ToString('N'))
$install = Join-Path $stage 'Install'
$agent = Join-Path $install 'agent'
New-Item -ItemType Directory -Force -Path $install | Out-Null
New-Item -ItemType Directory -Force -Path $agent | Out-Null
Copy-Item -LiteralPath (Join-Path $out 'ArcGISAgent.dll') -Destination $install -Force
Copy-Item -LiteralPath (Join-Path $out 'Markdig.dll') -Destination $install -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'VERSION') -Destination $agent -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'pyproject.toml') -Destination $agent -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'src') -Destination $agent -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'config') -Destination $agent -Recurse -Force
$daml = Get-Content -LiteralPath (Join-Path $repoRoot 'addin\ArcGISAgent\Config.daml') -Raw
$daml = [regex]::Replace($daml, '(<AddInInfo\b[^>]*\bversion=")[^"]+', { param($match) $match.Groups[1].Value + $agentVersion }, 1)
[IO.File]::WriteAllText((Join-Path $stage 'Config.daml'), $daml, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $install 'agent-home.txt'), 'agent', [Text.UTF8Encoding]::new($false))
$zip = Join-Path $out 'ArcGISAgent.zip'
$addin = Join-Path $out 'ArcGISAgent.esriAddinX'
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
Move-Item -LiteralPath $zip -Destination $addin -Force
Write-Output "版本：$agentVersion"
Write-Output "插件包：$addin"
