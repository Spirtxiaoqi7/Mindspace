[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Build requires PowerShell 7 or newer.'
}

npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
npm --prefix desktop run build
uv build --wheel
$wheel = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'dist') -Filter '*.whl' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $wheel) {
    throw 'Wheel build did not produce an artifact.'
}

Write-Output "WHEEL=$($wheel.FullName)"
