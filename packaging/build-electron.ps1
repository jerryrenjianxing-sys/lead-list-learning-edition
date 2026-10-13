$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    & '.\.venv\Scripts\python.exe' (Join-Path $PSScriptRoot 'bootstrap-electron.py')
    if ($LASTEXITCODE -ne 0) { throw 'Electron distribution verification failed' }
    & npm.cmd --prefix electron ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Electron dependency preparation failed' }
    & npm.cmd --prefix electron run test
    if ($LASTEXITCODE -ne 0) { throw 'Electron desktop tests failed' }
    & npm.cmd --prefix electron run package
    if ($LASTEXITCODE -ne 0) { throw 'Electron desktop packaging failed' }
    $shellDirectory = Join-Path $projectRoot 'build\electron\MediaWorkbench-win32-x64'
    if (-not (Test-Path -LiteralPath (Join-Path $shellDirectory 'MediaWorkbench.exe'))) { throw 'Forge did not produce the expected Windows executable' }
    Get-Item (Join-Path $shellDirectory 'MediaWorkbench.exe') | Select-Object Name,Length
} finally { Pop-Location }
