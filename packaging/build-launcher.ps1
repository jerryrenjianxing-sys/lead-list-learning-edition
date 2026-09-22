param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\build\launcher'))
$ErrorActionPreference = 'Stop'
$cache = Join-Path $PSScriptRoot 'cache'
New-Item -ItemType Directory -Force -Path $cache,$OutputDirectory | Out-Null
function Package([string]$name,[string]$version,[string]$folder) {
    $destination = Join-Path $cache $folder
    $archive = Join-Path $cache "$name.$version.zip"
    $locked = Get-Content (Join-Path $PSScriptRoot 'nuget.lock.json') -Raw | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath $archive)) { Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/$name/$version/$name.$version.nupkg" -OutFile $archive }
    $expected = $locked."$name.$version.zip"
    if (-not $expected -or (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant() -ne $expected) { throw "SDK checksum mismatch: $name $version" }
    if (-not (Test-Path -LiteralPath $destination)) {
        Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
    }
    return $destination
}
$web = Package 'microsoft.web.webview2' '1.0.4191.47' 'webview-sdk'
$velo = Package 'velopack' '1.2.0' 'velopack-sdk'
$json = Package 'newtonsoft.json' '13.0.4' 'json-sdk'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$references = @((Join-Path $web 'lib\net462\Microsoft.Web.WebView2.Core.dll'),(Join-Path $web 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'),(Join-Path $velo 'lib\net472\Velopack.dll'),(Join-Path $json 'lib\net45\Newtonsoft.Json.dll'))
$compileArgs = @('/nologo','/codepage:65001','/target:winexe','/platform:x64','/optimize+','/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll','/reference:System.Windows.Forms.dll','/reference:System.Net.Http.dll','/reference:System.Web.Extensions.dll','/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll',"/win32manifest:$(Join-Path $PSScriptRoot 'app.manifest')","/out:$(Join-Path $OutputDirectory 'MediaWorkbench.exe')")
foreach ($reference in $references) { $compileArgs += "/reference:$reference" }
$compileArgs += "/win32icon:$(Join-Path $PSScriptRoot '..\webui\public\brand\media-deep-researcher.ico')"
$compileArgs += (Join-Path $PSScriptRoot '..\launcher\MediaWorkbench.cs')
& $compiler @compileArgs
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
foreach ($reference in $references) { Copy-Item -LiteralPath $reference -Destination $OutputDirectory -Force }
Copy-Item -LiteralPath (Join-Path $web 'runtimes\win-x64\native\WebView2Loader.dll') -Destination $OutputDirectory -Force
Get-Item (Join-Path $OutputDirectory 'MediaWorkbench.exe') | Select-Object Name,Length
