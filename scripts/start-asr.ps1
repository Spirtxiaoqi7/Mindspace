[CmdletBinding()]
param([string]$Device = 'cuda:0')

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvRoot = if ($env:MINDSPACE_ASR_VENV) { $env:MINDSPACE_ASR_VENV } else { Join-Path $ProjectRoot '.venv-asr' }
$PythonExe = Join-Path $VenvRoot 'Scripts\python.exe'
$ReadyMarker = Join-Path $VenvRoot '.mindspace-asr-ready.json'
$ModelRoot = if ($env:MINDSPACE_MODEL_ROOT) { Join-Path $env:MINDSPACE_MODEL_ROOT 'asr' } else { Join-Path $ProjectRoot 'assets\models\asr' }
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'ASR environment is missing. Run scripts\prepare-asr.ps1 first.'
}
if (-not (Test-Path -LiteralPath $ReadyMarker)) {
    throw 'ASR environment is incomplete. Run scripts\prepare-asr.ps1 to repair it.'
}
Set-Location $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
& $PythonExe -m mindspace_graph.asr_worker --device $Device --model-root $ModelRoot
