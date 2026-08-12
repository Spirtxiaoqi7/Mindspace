[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Alias('Home')]
    [string]$MindspaceHome,
    [ValidateRange(1, 65535)]
    [int]$Port = 8876,
    [string]$EnvironmentRoot = '',
    [switch]$Desktop,
    [switch]$OpenBrowser,
    [switch]$DryRun,
    [int]$HealthTimeoutSeconds = 30
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StartScript = Join-Path $PSScriptRoot 'start.ps1'

function Resolve-DeploymentPath([string]$Value) {
    return [IO.Path]::GetFullPath($Value)
}

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Mindspace source deployment requires '$Name' on PATH."
    }
}

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Mindspace source deployment requires PowerShell 7 or newer.'
}

$resolvedHome = Resolve-DeploymentPath $MindspaceHome
$defaultEnvironment = if ($EnvironmentRoot) { $EnvironmentRoot } else { Join-Path $resolvedHome 'environment\core' }
$resolvedEnvironment = Resolve-DeploymentPath $defaultEnvironment
$logPath = Join-Path $resolvedHome 'logs\source-bootstrap.log'
$healthUrl = "http://127.0.0.1:$Port/api/v1/health"
$desktopProfile = Join-Path $resolvedHome 'environment\state\electron-profile'
$plan = [ordered]@{
    project_root = $ProjectRoot
    home = $resolvedHome
    environment_root = $resolvedEnvironment
    data_root = (Join-Path $resolvedHome 'data')
    model_root = (Join-Path $resolvedHome 'models')
    log_path = $logPath
    port = $Port
    health_url = $healthUrl
    desktop = [bool]$Desktop
    desktop_profile = $desktopProfile
    steps = @(
        'uv sync --frozen --extra embeddings'
        'npm --prefix frontend ci'
        'npm --prefix frontend run build'
        $(if ($Desktop) { 'npm --prefix desktop ci' })
        $(if ($Desktop) { 'npm --prefix desktop run build' })
        'start Core'
        'probe health'
        $(if ($Desktop) { 'start Electron desktop' })
    ) | Where-Object { $_ }
}
if ($DryRun) {
    $plan | ConvertTo-Json -Compress
    return
}

Require-Command 'uv'
Require-Command 'npm'
New-Item -ItemType Directory -Force -Path $resolvedHome, (Join-Path $resolvedHome 'logs') | Out-Null
Set-Location $ProjectRoot

try {
    & npm --prefix (Join-Path $ProjectRoot 'frontend') ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm --prefix (Join-Path $ProjectRoot 'frontend') run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    $index = Join-Path $ProjectRoot 'src\mindspace_graph\static\app\index.html'
    if (-not (Test-Path -LiteralPath $index -PathType Leaf)) { throw "Frontend build did not produce: $index" }

    if ($Desktop) {
        & npm --prefix (Join-Path $ProjectRoot 'desktop') ci
        if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
        & npm --prefix (Join-Path $ProjectRoot 'desktop') run build
        if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
        $desktopIndex = Join-Path $ProjectRoot 'desktop\dist\index.html'
        if (-not (Test-Path -LiteralPath $desktopIndex -PathType Leaf)) { throw "Desktop build did not produce: $desktopIndex" }
    }

    $startedRaw = & $StartScript -Home $resolvedHome -Port $Port -EnvironmentRoot $resolvedEnvironment -Sync -Background -LogPath $logPath
    if ($LASTEXITCODE -ne 0) { throw 'Core start command failed.' }
    $started = $startedRaw | ConvertFrom-Json
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    $healthy = $false
    do {
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            if ($health.ok -and ([IO.Path]::GetFullPath([string]$health.runtime_dir) -eq $resolvedHome)) {
                $healthy = $true
                break
            }
        }
        catch { Start-Sleep -Milliseconds 500 }
    } while ((Get-Date) -lt $deadline)
    if (-not $healthy) {
        if ($started.process_id) { Stop-Process -Id $started.process_id -Force -ErrorAction SilentlyContinue }
        throw "Mindspace Core did not pass health check at $healthUrl. Logs: $logPath and $logPath.stderr"
    }
    if ($Desktop) {
        New-Item -ItemType Directory -Force -Path $desktopProfile | Out-Null
        $env:MINDSPACE_HOME = $resolvedHome
        $env:MINDSPACE_RUNTIME_DIR = $resolvedHome
        $env:MINDSPACE_PORT = [string]$Port
        $env:MINDSPACE_SOURCE_DEPLOYMENT = '1'
        Write-Host "Mindspace Core is ready. Opening the source desktop on port $Port..."
        & npm --prefix (Join-Path $ProjectRoot 'desktop') run electron -- "--user-data-dir=$desktopProfile"
        if ($LASTEXITCODE -ne 0) { throw "Electron desktop exited with code $LASTEXITCODE." }
    }
    elseif ($OpenBrowser) { Start-Process "http://127.0.0.1:$Port/" }
    [ordered]@{ ok = $true; mode = $(if ($Desktop) { 'desktop' } else { 'browser' }); url = "http://127.0.0.1:$Port/"; home = $resolvedHome; log_path = $logPath; process_id = $started.process_id } | ConvertTo-Json -Compress
}
catch {
    throw "Mindspace source deployment failed. Log: $logPath. $($_.Exception.Message)"
}
finally {
    if ($Desktop -and $started.process_id) {
        Stop-Process -Id ([int]$started.process_id) -Force -ErrorAction SilentlyContinue
    }
}
