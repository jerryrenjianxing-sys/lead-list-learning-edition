$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $projectRoot 'build/launcher'
$output = Join-Path $projectRoot '.cache/update-tests'
New-Item -ItemType Directory -Force -Path $output | Out-Null
Get-ChildItem -LiteralPath $launcher -Filter '*.dll' | Copy-Item -Destination $output -Force
$references = @('/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll','/reference:System.Windows.Forms.dll','/reference:System.Net.Http.dll','/reference:System.Web.Extensions.dll')
foreach ($file in (Get-ChildItem -LiteralPath $launcher -Filter '*.dll' | Where-Object Name -ne 'WebView2Loader.dll')) { $references += "/reference:$($file.FullName)" }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$exe = Join-Path $output 'UpdateChecks.exe'
& $compiler /nologo /codepage:65001 /target:exe /platform:x64 /main:UpdateChecks "/out:$exe" @references (Join-Path $projectRoot 'launcher/MediaWorkbench.cs') (Join-Path $projectRoot 'launcher/Updates.cs') (Join-Path $projectRoot 'tests/UpdateChecks.cs')
if ($LASTEXITCODE -ne 0) { throw 'Update test compilation failed' }
& $exe
if ($LASTEXITCODE -ne 0) { throw 'Update tests failed' }
