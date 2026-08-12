[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) "mindspace-source-bootstrap-$([guid]::NewGuid().ToString('N'))"
try {
    $deploymentHome = Join-Path $TempRoot 'isolated-home'
    $environment = Join-Path $deploymentHome 'custom-environment'
    $bootstrap = & (Join-Path $PSScriptRoot 'bootstrap-source.ps1') -Home $deploymentHome -Port 8876 -EnvironmentRoot $environment -Desktop -DryRun
    if (-not $?) { throw "Bootstrap dry-run failed: $bootstrap" }
    $plan = $bootstrap | ConvertFrom-Json
    if ($plan.home -ne [IO.Path]::GetFullPath($deploymentHome)) { throw 'Bootstrap did not retain the explicit isolated Home' }
    if ($plan.environment_root -ne [IO.Path]::GetFullPath($environment)) { throw 'Bootstrap did not retain the explicit environment root' }
    if ($plan.port -ne 8876) { throw 'Bootstrap did not retain port 8876' }
    if (-not $plan.desktop) { throw 'Bootstrap did not retain desktop mode' }
    if ($plan.desktop_profile -ne (Join-Path ([IO.Path]::GetFullPath($deploymentHome)) 'environment\state\electron-profile')) { throw 'Bootstrap desktop profile escaped the isolated Home' }
    if ($plan.steps -notcontains 'npm --prefix desktop run build' -or $plan.steps -notcontains 'start Electron desktop') { throw 'Bootstrap desktop plan is incomplete' }
    if (Test-Path -LiteralPath $deploymentHome) { throw 'Bootstrap dry-run created an isolated Home' }

    $start = & (Join-Path $PSScriptRoot 'start.ps1') -Home $deploymentHome -Port 8876 -EnvironmentRoot $environment -DryRun
    if (-not $?) { throw "Start dry-run failed: $start" }
    $startPlan = $start | ConvertFrom-Json
    if ($startPlan.data_root -ne (Join-Path ([IO.Path]::GetFullPath($deploymentHome)) 'data')) { throw 'Start plan did not isolate data root' }
    if ($startPlan.model_root -ne (Join-Path ([IO.Path]::GetFullPath($deploymentHome)) 'models')) { throw 'Start plan did not isolate model root' }
    if ($startPlan.port -ne 8876) { throw 'Start plan did not retain port 8876' }

    $portable = & (Join-Path $PSScriptRoot 'portable-start.ps1') -Home $deploymentHome -Port 8876 -EnvironmentRoot $environment -DryRun
    if (-not $?) { throw "Portable dry-run failed: $portable" }
    $portablePlan = $portable | ConvertFrom-Json
    if ($portablePlan.home -ne [IO.Path]::GetFullPath($deploymentHome)) { throw 'Portable plan did not retain the explicit isolated Home' }
    if ($portablePlan.port -ne 8876) { throw 'Portable plan did not retain port 8876' }

    [ordered]@{ ok = $true; isolated_home = $deploymentHome; port = 8876; no_clone = $true; no_home_created = $true } | ConvertTo-Json -Compress
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
