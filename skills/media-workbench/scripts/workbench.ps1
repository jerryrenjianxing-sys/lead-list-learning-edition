# Forward arguments without parsing or string interpolation into shell commands.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$instancePath = if ($env:MEDIAWORKBENCH_INSTANCE) { $env:MEDIAWORKBENCH_INSTANCE } else { Join-Path $env:LOCALAPPDATA 'MediaWorkbench\instance.json' }
if (-not (Test-Path -LiteralPath $instancePath)) {
    Write-Output '{"ok":false,"error":"请先启动Media Deep Researcher，当前没有找到本机实例。"}'
    exit 1
}
try {
    $instance = Get-Content -LiteralPath $instancePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($instance.product -ne 'MediaWorkbench' -or $instance.api_version -ne 1) { throw '实例不是兼容的Media Deep Researcher。' }
    $pythonPath = [string]$instance.python
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '软件内置 Python 不存在，请重新启动软件。' }
    & $pythonPath (Join-Path $PSScriptRoot 'workbench.py') --instance $instancePath @args
    exit $LASTEXITCODE
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
