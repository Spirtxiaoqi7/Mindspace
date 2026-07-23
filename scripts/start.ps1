[CmdletBinding()]
param(
    [switch]$OpenBrowser,
    [switch]$Sync
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Mindspace Graph requires PowerShell 7 or newer.'
}

$PythonExe = if ($env:MINDSPACE_CORE_PYTHON) { $env:MINDSPACE_CORE_PYTHON } else { Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
$UvExe = if ($env:MINDSPACE_UV) { $env:MINDSPACE_UV } else { 'uv' }
if (-not $env:MINDSPACE_HOME -and ($Sync -or -not (Test-Path -LiteralPath $PythonExe))) {
    & $UvExe sync --extra embeddings
}

$port = if ($env:MINDSPACE_PORT) { [int]$env:MINDSPACE_PORT } else { 8765 }
$url = "http://127.0.0.1:$port/"

if ($OpenBrowser) {
    Start-Job -ScriptBlock {
        param($Target)
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try {
                Invoke-WebRequest -Uri $Target -UseBasicParsing -TimeoutSec 1 | Out-Null
                Start-Process $Target
                return
            }
            catch {
                Start-Sleep -Milliseconds 300
            }
        }
    } -ArgumentList $url | Out-Null
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Mindspace private Python environment is missing. Run Launcher one-click initialization first.'
}
& $PythonExe -m mindspace_graph.server
