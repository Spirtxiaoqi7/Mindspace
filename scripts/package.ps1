[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DistRoot = Join-Path $ProjectRoot 'dist'
$BundleRoot = Join-Path $DistRoot 'mindspace-graph-portable'

& (Join-Path $PSScriptRoot 'build.ps1')
npm --prefix (Join-Path $ProjectRoot 'desktop') run dist

if (Test-Path -LiteralPath $BundleRoot) {
    $resolvedDist = (Resolve-Path $DistRoot).Path
    $resolvedBundle = (Resolve-Path $BundleRoot).Path
    if (-not $resolvedBundle.StartsWith($resolvedDist, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean bundle outside dist: $resolvedBundle"
    }
    Remove-Item -LiteralPath $resolvedBundle -Recurse -Force
}
New-Item -ItemType Directory -Path $BundleRoot | Out-Null

$wheel = Get-ChildItem -LiteralPath $DistRoot -Filter '*.whl' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
Copy-Item -LiteralPath $wheel.FullName -Destination $BundleRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portable-start.ps1') -Destination $BundleRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'config\.env.example') -Destination (Join-Path $BundleRoot '.env.example')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination $BundleRoot

$zipPath = Join-Path $DistRoot 'mindspace-graph-portable.zip'
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $BundleRoot '*') -DestinationPath $zipPath -CompressionLevel Optimal
Write-Output "PORTABLE_ZIP=$zipPath"
