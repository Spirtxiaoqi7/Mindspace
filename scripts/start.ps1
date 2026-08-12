[CmdletBinding()]
param(
    [Alias('Home')]
    [string]$MindspaceHome = '',
    [int]$Port = 0,
    [string]$EnvironmentRoot = '',
    [switch]$OpenBrowser,
    [switch]$Sync,
    [switch]$Verify,
    [switch]$Background,
    [switch]$DryRun,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'service-ports.ps1')
Set-Location $ProjectRoot

function Resolve-MindspacePath([string]$Value, [string]$Fallback) {
    $candidate = if ($Value) { $Value } else { $Fallback }
    return [IO.Path]::GetFullPath($candidate)
}

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Mindspace source deployment requires PowerShell 7 or newer.'
}

$defaultHome = if ($env:MINDSPACE_HOME) { $env:MINDSPACE_HOME } else { Join-Path $ProjectRoot 'runtime' }
$resolvedHome = Resolve-MindspacePath $MindspaceHome $defaultHome
$resolvedEnvironment = Resolve-MindspacePath $EnvironmentRoot (Join-Path $resolvedHome 'environment\core')
$resolvedData = Join-Path $resolvedHome 'data'
$resolvedModels = Join-Path $resolvedHome 'models'
$resolvedLogs = Join-Path $resolvedHome 'logs'

if ($Port -gt 0) {
    if ($Port -gt 65535) { throw "Invalid Mindspace Core port: $Port" }
    $env:MINDSPACE_PORT = [string]$Port
}
$ServicePorts = Get-MindspaceServicePorts -ProjectRoot $ProjectRoot
$resolvedPort = [int]$ServicePorts.core

$env:MINDSPACE_HOME = $resolvedHome
$env:MINDSPACE_RUNTIME_DIR = $resolvedHome
$env:MINDSPACE_DATA_ROOT = $resolvedData
$env:MINDSPACE_MODEL_ROOT = $resolvedModels
$env:MINDSPACE_PORT = [string]$resolvedPort
$env:UV_PROJECT_ENVIRONMENT = $resolvedEnvironment
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'

$PythonExe = if ($env:MINDSPACE_CORE_PYTHON) { $env:MINDSPACE_CORE_PYTHON } else { Join-Path $resolvedEnvironment 'Scripts\python.exe' }
$UvExe = if ($env:MINDSPACE_UV) { $env:MINDSPACE_UV } else { 'uv' }
$resolvedLog = Resolve-MindspacePath $LogPath (Join-Path $resolvedLogs 'core-source.log')
$url = "http://127.0.0.1:$resolvedPort/"

$plan = [ordered]@{
    project_root = $ProjectRoot
    home = $resolvedHome
    environment_root = $resolvedEnvironment
    data_root = $resolvedData
    model_root = $resolvedModels
    log_path = $resolvedLog
    port = $resolvedPort
    url = $url
    python = $PythonExe
    sync_required = [bool]($Sync -or -not (Test-Path -LiteralPath $PythonExe))
}
if ($DryRun) {
    $plan | ConvertTo-Json -Compress
    return
}

New-Item -ItemType Directory -Force -Path $resolvedHome, $resolvedData, $resolvedModels, $resolvedLogs | Out-Null
if ($plan.sync_required) {
    & $UvExe sync --frozen --extra embeddings
    if ($LASTEXITCODE -ne 0) { throw "Mindspace Python environment synchronization failed. Log: $resolvedLog" }
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Mindspace source Python environment is missing after synchronization: $PythonExe"
}

if ($Verify) {
    & (Join-Path $PSScriptRoot 'runtime-verify.ps1')
}

if ($Background) {
    $stderr = "$resolvedLog.stderr"
    $process = Start-Process -FilePath $PythonExe -ArgumentList @('-m', 'mindspace_graph.server') -WorkingDirectory $ProjectRoot -RedirectStandardOutput $resolvedLog -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    [ordered]@{ process_id = $process.Id; url = $url; log_path = $resolvedLog; error_log_path = $stderr; home = $resolvedHome } | ConvertTo-Json -Compress
    return
}

if ($OpenBrowser) {
    Start-Job -ScriptBlock {
        param($Target)
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try {
                Invoke-WebRequest -Uri $Target -UseBasicParsing -TimeoutSec 1 | Out-Null
                Start-Process $Target
                return
            }
            catch { Start-Sleep -Milliseconds 300 }
        }
    } -ArgumentList $url | Out-Null
}

try {
    & $PythonExe -m mindspace_graph.server 2>&1 | Tee-Object -FilePath $resolvedLog -Append
}
catch {
    throw "Mindspace Core exited unexpectedly. Log: $resolvedLog. $($_.Exception.Message)"
}
