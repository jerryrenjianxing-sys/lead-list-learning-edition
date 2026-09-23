param([string]$Version='', [string]$Tag='')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $sourceVersion=[regex]::Match((Get-Content (Join-Path $projectRoot 'pyproject.toml') -Raw), '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"').Groups[1].Value
    if (-not $Version) { $Version=$sourceVersion }
    if ($Version -ne $sourceVersion -or ($Tag -and $Tag -ne "v$Version")) { throw 'Tag and package must match pyproject.toml version' }
    $releaseDirectory=Join-Path $projectRoot "release\$Version"
    if (Test-Path -LiteralPath (Join-Path $releaseDirectory 'build-integrity.json')) { throw 'This version has already been built. Use a clean build checkout.' }
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
    & $toolchain.Dotnet $toolchain.Vpk pack --packId MediaWorkbench.Desktop --packVersion $Version --channel win-preview --packDir (Join-Path $projectRoot 'build\MediaWorkbench') --mainExe MediaWorkbench.exe --packTitle 'Media Deep Researcher' --icon (Join-Path $projectRoot 'webui\public\brand\media-deep-researcher.ico') --packAuthors 'MediaWorkbench contributors' --releaseNotes (Join-Path $projectRoot 'docs\RELEASE_NOTES.md') --outputDir $releaseDirectory --delta None
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer packaging failed' }
    & '.\.venv\Scripts\python.exe' (Join-Path $PSScriptRoot 'collect_delivery.py') --version $Version --release-directory $releaseDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Delivery archive or integrity verification failed' }
} finally { Pop-Location }
