param([string]$ArcGISProBin = 'E:\programs\ArcGIS\bin')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$project = Join-Path $repoRoot 'addin\ArcGISAgent\ArcGISAgent.csproj'
$localDotnet = Join-Path $repoRoot '.dotnet\dotnet.exe'
$dotnet = if (Test-Path -LiteralPath $localDotnet) { $localDotnet } else { 'dotnet' }
& $dotnet build $project -c Release "-p:ArcGISProBin=$ArcGISProBin" --nologo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$out = Join-Path $repoRoot 'addin\ArcGISAgent\bin\Release\net8.0-windows'
$stage = Join-Path $out ('package-' + [Guid]::NewGuid().ToString('N'))
$install = Join-Path $stage 'Install'
New-Item -ItemType Directory -Force -Path $install | Out-Null
Copy-Item -LiteralPath (Join-Path $out 'ArcGISAgent.dll') -Destination $install -Force
Copy-Item -LiteralPath (Join-Path $repoRoot 'addin\ArcGISAgent\Config.daml') -Destination $stage -Force
[IO.File]::WriteAllText((Join-Path $install 'agent-home.txt'), $repoRoot, [Text.UTF8Encoding]::new($false))
$zip = Join-Path $out 'ArcGISAgent.zip'
$addin = Join-Path $out 'ArcGISAgent.esriAddinX'
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
Move-Item -LiteralPath $zip -Destination $addin -Force
Write-Output "插件包：$addin"
