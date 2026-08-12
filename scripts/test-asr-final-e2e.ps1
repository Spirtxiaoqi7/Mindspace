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
$ShutdownToken = [Guid]::NewGuid().ToString('N')

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
$env:MINDSPACE_SERVICE_SHUTDOWN_TOKEN = $ShutdownToken
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
        try {
            Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/shutdown" -Headers @{ 'X-Mindspace-Service-Token' = $ShutdownToken } -TimeoutSec 3 | Out-Null
            if (-not $Worker.WaitForExit(30000)) { throw 'ASR worker did not exit gracefully within 30 seconds' }
        } catch {
            Write-Error "ASR graceful shutdown failed; process was left intact for diagnosis: $($_.Exception.Message)"
        }
    }
    Start-Sleep -Milliseconds 500
    Write-Output 'ASR_E2E_WORKER_STDERR_TAIL'
    Get-Content -LiteralPath $Stderr -Tail 30 -ErrorAction SilentlyContinue
}
