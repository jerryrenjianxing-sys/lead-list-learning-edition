param(
    [string]$VelopackVersion = '1.2.0',
    [string]$DotnetRuntimeVersion = '8.0.30'
)

$ErrorActionPreference = 'Stop'
$toolsRoot = Join-Path $PSScriptRoot 'tools'
$vpkPackageRoot = Join-Path $toolsRoot 'vpk-package'
$vpkArchive = Join-Path $vpkPackageRoot "vpk.$VelopackVersion.nupkg"
$vpkExpanded = Join-Path $vpkPackageRoot 'expanded'
$vpkDll = Join-Path $vpkExpanded 'tools\net8.0\any\vpk.dll'
$runtimeRoot = Join-Path $toolsRoot 'dotnet-runtime'
$runtimeArchive = Join-Path $runtimeRoot "dotnet-runtime-$DotnetRuntimeVersion-win-x64.zip"
$runtimeExpanded = Join-Path $runtimeRoot 'expanded'
$dotnet = Join-Path $runtimeExpanded 'dotnet.exe'

$vpkUrl = "https://github.com/velopack/velopack/releases/download/$VelopackVersion/vpk.$VelopackVersion.nupkg"
$vpkSha256 = '3e458a676be46d1122e522312db18411f36ea8c70e586f81a676695d43f89dbc'
$runtimeUrl = "https://builds.dotnet.microsoft.com/dotnet/Runtime/$DotnetRuntimeVersion/dotnet-runtime-$DotnetRuntimeVersion-win-x64.zip"
$runtimeSha512 = '99e61c9a2d15dbb280db98bfc3ee45dfeda25fdb91e3d3c167789dd74328957a4f791c57ad13e8a3344df64a27d6ef8332dd91a773072541789a1d11ee3b4439'

New-Item -ItemType Directory -Force -Path $vpkPackageRoot, $runtimeRoot | Out-Null

if (-not (Test-Path -LiteralPath $vpkArchive)) {
    & curl.exe -L --fail --retry 3 --output $vpkArchive $vpkUrl
    if ($LASTEXITCODE -ne 0) { throw 'Velopack download failed.' }
}
if ((Get-FileHash -Algorithm SHA256 $vpkArchive).Hash.ToLowerInvariant() -ne $vpkSha256) {
    throw 'Velopack SHA256 mismatch.'
}
if (-not (Test-Path -LiteralPath $vpkDll)) {
    $zip = Join-Path $vpkPackageRoot "vpk.$VelopackVersion.zip"
    Copy-Item -LiteralPath $vpkArchive -Destination $zip -Force
    Expand-Archive -LiteralPath $zip -DestinationPath $vpkExpanded -Force
}

if (-not (Test-Path -LiteralPath $runtimeArchive)) {
    & curl.exe -L --fail --retry 3 --output $runtimeArchive $runtimeUrl
    if ($LASTEXITCODE -ne 0) { throw '.NET runtime download failed.' }
}
if ((Get-FileHash -Algorithm SHA512 $runtimeArchive).Hash.ToLowerInvariant() -ne $runtimeSha512) {
    throw '.NET runtime SHA512 mismatch.'
}
if (-not (Test-Path -LiteralPath $dotnet)) {
    Expand-Archive -LiteralPath $runtimeArchive -DestinationPath $runtimeExpanded -Force
}

$help = & $dotnet $vpkDll -h
if ($LASTEXITCODE -ne 0 -or ($help -join "`n") -notmatch "Velopack CLI $VelopackVersion") {
    throw 'Velopack verification failed.'
}

[pscustomobject]@{
    Dotnet = $dotnet
    DotnetRuntimeVersion = $DotnetRuntimeVersion
    Vpk = $vpkDll
    VpkVersion = $VelopackVersion
}
