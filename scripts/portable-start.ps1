[CmdletBinding()]
param(
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
$BundleRoot = $PSScriptRoot
. (Join-Path $PSScriptRoot 'scripts\service-ports.ps1')
$ServicePorts = Get-MindspaceServicePorts -ProjectRoot $BundleRoot
$Wheel = Get-ChildItem -LiteralPath $BundleRoot -Filter '*.whl' | Select-Object -First 1

if (-not $Wheel) {
    throw 'Portable bundle is missing the application wheel.'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install it first, then run this script again.'
}

$env:MINDSPACE_RUNTIME_DIR = Join-Path $BundleRoot 'runtime'
uv venv (Join-Path $BundleRoot '.venv')
uv pip install --python (Join-Path $BundleRoot '.venv\Scripts\python.exe') $Wheel.FullName

if (Test-Path -LiteralPath (Join-Path $BundleRoot '.env')) {
    Get-Content -LiteralPath (Join-Path $BundleRoot '.env') | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
        }
    }
}

$port = $ServicePorts.core
if ($OpenBrowser) {
    Start-Process "http://127.0.0.1:$port/"
}

& (Join-Path $BundleRoot '.venv\Scripts\mindspace-server.exe')
