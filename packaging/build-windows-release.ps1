param([string]$Version='0.1.1')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $sourceVersion=(Get-Content (Join-Path $projectRoot 'workbench\__init__.py') -Raw) -replace '(?s).*__version__\s*=\s*"([^"]+)".*','$1'
    if ($Version -ne $sourceVersion.Trim()) { throw 'Package version must match workbench/__init__.py and launcher assembly version' }
    & uv sync --locked --python 3.11.16
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency preparation failed' }
    & npm.cmd --prefix webui ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency preparation failed' }
    & npm.cmd --prefix webui run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    & (Join-Path $PSScriptRoot 'build-launcher.ps1')
    & '.\.venv\Scripts\python.exe' (Join-Path $PSScriptRoot 'stage.py')
    if ($LASTEXITCODE -ne 0) { throw 'Release staging failed' }
    $toolchain=& (Join-Path $PSScriptRoot 'bootstrap-velopack.ps1')
    & $toolchain.Dotnet $toolchain.Vpk pack --packId MediaWorkbench.Desktop --packVersion $Version --packDir (Join-Path $projectRoot 'build\MediaWorkbench') --mainExe MediaWorkbench.exe --packTitle 'Media Deep Researcher' --icon (Join-Path $projectRoot 'webui\public\brand\media-deep-researcher.ico') --packAuthors 'MediaWorkbench contributors' --outputDir (Join-Path $projectRoot 'release') --delta None
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer packaging failed' }
    & '.\.venv\Scripts\python.exe' (Join-Path $PSScriptRoot 'collect_delivery.py') --version $Version
    if ($LASTEXITCODE -ne 0) { throw 'Delivery archive or integrity verification failed' }
} finally { Pop-Location }
