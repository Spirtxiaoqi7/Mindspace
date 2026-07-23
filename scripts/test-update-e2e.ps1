[CmdletBinding()]
param(
    [string]$WebRoot = '',
    [string]$UpdateUrl = 'http://127.0.0.1:9780/manifest.json',
    [string]$CurrentVersion = '0.0.0',
    [string]$TargetVersion = '',
    [int]$Port = 9780
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimeRoot = Join-Path $ProjectRoot 'runtime'
$TestRoot = Join-Path $RuntimeRoot 'update-e2e'
if (Test-Path -LiteralPath $TestRoot) {
    $Resolved = (Resolve-Path $TestRoot).Path
    if (-not $Resolved.StartsWith($RuntimeRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe update test path' }
    Remove-Item -LiteralPath $Resolved -Recurse -Force
}
$AppRoot = Join-Path $TestRoot 'app'
$UserData = Join-Path $TestRoot 'user-data'
New-Item -ItemType Directory -Path (Join-Path $AppRoot 'scripts'), $UserData -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'pyproject.toml') -Destination $AppRoot
$FixtureProject = Join-Path $AppRoot 'pyproject.toml'
$FixtureText = Get-Content -LiteralPath $FixtureProject -Raw
$FixtureText = [regex]::Replace($FixtureText, '(?m)^version\s*=\s*"[^"]+"', "version = `"$CurrentVersion`"", 1)
Set-Content -LiteralPath $FixtureProject -Value $FixtureText -Encoding utf8
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'apply-update.ps1') -Destination (Join-Path $AppRoot 'scripts\apply-update.ps1')

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ServeDirectory = if ($WebRoot) { [IO.Path]::GetFullPath($WebRoot) } else { Join-Path $ProjectRoot 'runtime\update-feed' }
if (-not $TargetVersion) {
    $ManifestPath = Join-Path $ServeDirectory 'manifest.json'
    if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Update test manifest is missing: $ManifestPath" }
    $TargetVersion = [string](Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json).version
}
if ($CurrentVersion -eq $TargetVersion) { throw 'CurrentVersion must differ from the update target' }
$Server = Start-Process -FilePath $Python -ArgumentList @(
    '-m', 'http.server', $Port, '--bind', '127.0.0.1',
    '--directory', $ServeDirectory
) -WindowStyle Hidden -PassThru
try {
    Start-Sleep -Milliseconds 500
    $env:UPDATE_E2E_ROOT = $AppRoot
    $env:UPDATE_E2E_USER = $UserData
    $env:UPDATE_E2E_PROJECT = $ProjectRoot
    $env:MINDSPACE_PWSH_TEST = (Get-Command pwsh).Source
    $env:UPDATE_E2E_URL = $UpdateUrl
    $env:UPDATE_E2E_CURRENT = $CurrentVersion
    $env:UPDATE_E2E_TARGET = $TargetVersion
    $env:UPDATE_E2E_LAUNCHER = [string](Get-Content -LiteralPath (Join-Path $ProjectRoot 'desktop\package.json') -Raw | ConvertFrom-Json).version
    node (Join-Path $ProjectRoot 'desktop\update-e2e.cjs')
    if ($LASTEXITCODE -ne 0) { throw 'Updater end-to-end test failed' }
}
finally {
    Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
    Remove-Item Env:UPDATE_E2E_URL, Env:UPDATE_E2E_CURRENT, Env:UPDATE_E2E_TARGET, Env:UPDATE_E2E_LAUNCHER -ErrorAction SilentlyContinue
}
