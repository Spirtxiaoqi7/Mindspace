[CmdletBinding()]
param(
    [string]$Device = 'cuda',
    [int]$Port = 5055
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TtsVenv = if ($env:MINDSPACE_TTS_VENV) { $env:MINDSPACE_TTS_VENV } else { Join-Path $ProjectRoot '.venv-tts' }
$AsrVenv = if ($env:MINDSPACE_ASR_VENV) { $env:MINDSPACE_ASR_VENV } else { Join-Path $ProjectRoot '.venv-asr' }
$LegacyPython = Join-Path $TtsVenv 'Scripts\python.exe'
$SharedPython = Join-Path $AsrVenv 'Scripts\python.exe'
$SharedMarker = if ($env:MINDSPACE_TTS_MARKER_ROOT) { Join-Path $env:MINDSPACE_TTS_MARKER_ROOT 'ready.json' } else { Join-Path $ProjectRoot 'runtime\components\tts-runtime\ready.json' }
$PythonExe = if ((Test-Path -LiteralPath $SharedMarker) -and (Test-Path -LiteralPath $SharedPython)) {
    $SharedPython
}
elseif (Test-Path -LiteralPath $LegacyPython) {
    $LegacyPython
}
else {
    $SharedPython
}
$Worker = Join-Path $ProjectRoot 'vendor\cosyvoice_mindspace_worker.py'
$ModelRoot = if ($env:MINDSPACE_MODEL_ROOT) { $env:MINDSPACE_MODEL_ROOT } else { Join-Path $ProjectRoot 'assets\models' }
$RuntimeRoot = if ($env:MINDSPACE_RUNTIME_DIR) { $env:MINDSPACE_RUNTIME_DIR } else { Join-Path $ProjectRoot 'runtime' }
$Model = Join-Path $ModelRoot 'tts\Fun-CosyVoice3-0.5B-2512'
$Reference = Join-Path $ProjectRoot 'assets\audio\tts-reference.wav'
$ReferenceText = ''
$SettingsPath = Join-Path $RuntimeRoot 'config\settings.json'
$SpeakerCache = Join-Path $RuntimeRoot 'data\audio\spk2info-mindspace.pt'
$CurrentSettings = $null
$TtsProvider = 'siliconflow'
if (Test-Path -LiteralPath $SettingsPath) {
    $CurrentSettings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    if ($CurrentSettings.audio.tts_provider) {
        $TtsProvider = ([string]$CurrentSettings.audio.tts_provider).ToLowerInvariant()
    }
    if ($CurrentSettings.audio.tts_reference_audio -and (Test-Path -LiteralPath $CurrentSettings.audio.tts_reference_audio)) {
        $Reference = $CurrentSettings.audio.tts_reference_audio
        $ReferenceText = [string]$CurrentSettings.audio.tts_reference_text
    }
}

if ($TtsProvider -eq 'gpt-sovits') {
    $GptVenv = if ($env:MINDSPACE_GPT_SOVITS_VENV) { $env:MINDSPACE_GPT_SOVITS_VENV } else { Join-Path $ProjectRoot '.venv-gpt-sovits' }
    $GptPython = Join-Path $GptVenv 'Scripts\python.exe'
    $GptMarker = Join-Path $GptVenv 'ready.json'
    $GptWorker = Join-Path $ProjectRoot 'vendor\gpt_sovits_mindspace_worker.py'
    $GptCodeRoot = if ($env:MINDSPACE_GPT_SOVITS_CODE_ROOT) { $env:MINDSPACE_GPT_SOVITS_CODE_ROOT } else { Join-Path $ProjectRoot 'vendor\GPT-SoVITS' }
    $GptRuntimeRoot = if ($env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT) { $env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT } else { Join-Path $ModelRoot 'tts\gpt-sovits\runtime' }
    $GptCatalog = Join-Path $ProjectRoot 'config\gpt-sovits-voices.json'
    $GptVoice = if ($CurrentSettings.audio.tts_gpt_sovits_voice) { [string]$CurrentSettings.audio.tts_gpt_sovits_voice } else { 'v4-changli' }
    foreach ($required in $GptPython, $GptMarker, $GptWorker, $GptCatalog, (Join-Path $GptCodeRoot 'GPT_SoVITS\TTS_infer_pack\TTS.py'), (Join-Path $GptRuntimeRoot 'GPT_SoVITS\pretrained_models\s1v3.ckpt')) {
        if (-not (Test-Path -LiteralPath $required)) { throw "GPT-SoVITS runtime is incomplete: $required" }
    }
    $arguments = @(
        $GptWorker,
        '--host', '127.0.0.1',
        '--port', $Port,
        '--code-root', $GptCodeRoot,
        '--runtime-root', $GptRuntimeRoot,
        '--model-root', $ModelRoot,
        '--catalog', $GptCatalog,
        '--voice', $GptVoice,
        '--device', $Device,
        '--fp16',
        '--warmup-text', '你好。'
    )
    if ($Device -eq 'cpu') { $arguments += '--force-cpu' }
    Set-Location $ProjectRoot
    & $GptPython @arguments
    exit $LASTEXITCODE
}

foreach ($required in $PythonExe, $Worker, $Model, $Reference) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "TTS runtime is incomplete: $required"
    }
}

$CosyVoiceRoot = Join-Path $ProjectRoot 'vendor\CosyVoice\cosyvoice\cli\cosyvoice.py'
if (-not (Test-Path -LiteralPath $CosyVoiceRoot)) {
    throw "TTS runtime is incomplete: $CosyVoiceRoot"
}

$arguments = @(
    $Worker,
    '--host', '127.0.0.1',
    '--port', $Port,
    '--model-dir', $Model,
    '--reference', $Reference,
    '--reference-text', $ReferenceText,
    '--speaker-cache', $SpeakerCache,
    '--fp16',
    '--warmup-text', '语音服务预热。'
)
if ($Device -eq 'cpu') {
    $arguments += '--force-cpu'
}
Set-Location $ProjectRoot
& $PythonExe @arguments
