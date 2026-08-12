[CmdletBinding()]
param(
    [Alias('Home')]
    [string]$MindspaceHome = '',
    [ValidateRange(1, 65535)]
    [int]$Port = 0,
    [string]$EnvironmentRoot = '',
    [switch]$OpenBrowser,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$BundleRoot = $PSScriptRoot
$portsScript = Join-Path $BundleRoot 'scripts\service-ports.ps1'
if (-not (Test-Path -LiteralPath $portsScript -PathType Leaf)) {
    $portsScript = Join-Path $BundleRoot 'service-ports.ps1'
}
. $portsScript
$defaultHome = if ($MindspaceHome) { $MindspaceHome } elseif ($env:MINDSPACE_HOME) { $env:MINDSPACE_HOME } else { Join-Path $BundleRoot 'MindspaceHome' }
$resolvedHome = [IO.Path]::GetFullPath($defaultHome)
$defaultEnvironment = if ($EnvironmentRoot) { $EnvironmentRoot } else { Join-Path $resolvedHome 'environment\core' }
$resolvedEnvironment = [IO.Path]::GetFullPath($defaultEnvironment)
if ($Port -gt 0) { $env:MINDSPACE_PORT = [string]$Port }
$portsProjectRoot = if (Test-Path -LiteralPath (Join-Path $BundleRoot 'config\service-ports.json') -PathType Leaf) { $BundleRoot } else { Split-Path -Parent $BundleRoot }
$ServicePorts = Get-MindspaceServicePorts -ProjectRoot $portsProjectRoot
$resolvedPort = [int]$ServicePorts.core
$env:MINDSPACE_HOME = $resolvedHome
$env:MINDSPACE_RUNTIME_DIR = $resolvedHome
$env:MINDSPACE_DATA_ROOT = Join-Path $resolvedHome 'data'
$env:MINDSPACE_MODEL_ROOT = Join-Path $resolvedHome 'models'
$env:MINDSPACE_PORT = [string]$resolvedPort
$env:UV_PROJECT_ENVIRONMENT = $resolvedEnvironment
$wheel = Get-ChildItem -LiteralPath $BundleRoot -Filter '*.whl' | Select-Object -First 1
$plan = [ordered]@{ bundle_root = $BundleRoot; home = $resolvedHome; environment_root = $resolvedEnvironment; data_root = $env:MINDSPACE_DATA_ROOT; model_root = $env:MINDSPACE_MODEL_ROOT; port = $resolvedPort; url = "http://127.0.0.1:$resolvedPort/"; wheel = if ($wheel) { $wheel.FullName } else { '' } }
if ($DryRun) { $plan | ConvertTo-Json -Compress; return }
if (-not $wheel) { throw 'Portable Core bundle is missing the application wheel.' }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Portable Core bundle requires uv. Install it first, then run this script again.' }
New-Item -ItemType Directory -Force -Path $resolvedHome, $env:MINDSPACE_DATA_ROOT, $env:MINDSPACE_MODEL_ROOT, (Join-Path $resolvedHome 'logs') | Out-Null
uv venv $resolvedEnvironment
uv pip install --python (Join-Path $resolvedEnvironment 'Scripts\python.exe') $wheel.FullName
if (Test-Path -LiteralPath (Join-Path $BundleRoot '.env')) {
    Get-Content -LiteralPath (Join-Path $BundleRoot '.env') | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process') }
    }
}
if ($OpenBrowser) { Start-Process "http://127.0.0.1:$resolvedPort/" }
& (Join-Path $resolvedEnvironment 'Scripts\mindspace-server.exe')
