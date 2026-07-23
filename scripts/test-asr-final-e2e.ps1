[CmdletBinding()]
param(
    [int]$Port = 8876,
    [int]$ReadyTimeoutSeconds = 100
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonExe = Join-Path $ProjectRoot '.venv-asr\Scripts\python.exe'
$ModelRoot = Join-Path $ProjectRoot 'assets\models\asr'
$LogRoot = Join-Path $ProjectRoot 'artifacts\asr-final-e2e'
$Stdout = Join-Path $LogRoot 'worker.stdout.log'
$Stderr = Join-Path $LogRoot 'worker.stderr.log'

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
$Worker = Start-Process -FilePath $PythonExe -ArgumentList @(
    '-m', 'mindspace_graph.asr_worker',
    '--host', '127.0.0.1',
    '--port', $Port,
    '--device', 'cuda:0',
    '--model-root', $ModelRoot
) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr -PassThru

Write-Output "ASR_E2E_PID=$($Worker.Id)"
try {
    $Ready = $false
    $Deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        Start-Sleep -Milliseconds 500
        try {
            $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($Health.ready) {
                $Ready = $true
                $Health | ConvertTo-Json -Depth 8
                break
            }
        }
        catch {
            if ($Worker.HasExited) {
                throw "ASR worker exited with code $($Worker.ExitCode)"
            }
        }
    }
    if (-not $Ready) {
        throw 'ASR worker readiness timeout'
    }
    & $PythonExe (Join-Path $PSScriptRoot 'smoke-asr.py') `
        --url "ws://127.0.0.1:$Port/ws"
    if ($LASTEXITCODE -ne 0) {
        throw "ASR smoke test failed with code $LASTEXITCODE"
    }
    Write-Output 'ASR_E2E=passed'
}
finally {
    if (-not $Worker.HasExited) {
        Stop-Process -Id $Worker.Id
    }
    Start-Sleep -Milliseconds 500
    Write-Output 'ASR_E2E_WORKER_STDERR_TAIL'
    Get-Content -LiteralPath $Stderr -Tail 30 -ErrorAction SilentlyContinue
}
