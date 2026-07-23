[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$CorePython = if ($env:MINDSPACE_CORE_PYTHON) { $env:MINDSPACE_CORE_PYTHON } else { Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
$AsrVenv = if ($env:MINDSPACE_ASR_VENV) { $env:MINDSPACE_ASR_VENV } else { Join-Path $ProjectRoot '.venv-asr' }
$AsrPython = Join-Path $AsrVenv 'Scripts\python.exe'
$ModelRoot = if ($env:MINDSPACE_MODEL_ROOT) { $env:MINDSPACE_MODEL_ROOT } else { Join-Path $ProjectRoot 'assets\models' }
$RuntimeRoot = if ($env:MINDSPACE_RUNTIME_DIR) { $env:MINDSPACE_RUNTIME_DIR } else { Join-Path $ProjectRoot 'runtime' }

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Mindspace requires PowerShell 7 or newer.'
}
if (-not (Test-Path -LiteralPath $CorePython)) {
    throw '主 Python 环境缺失，请先运行“依赖修复”。'
}

& $CorePython -c 'import fastapi, mindspace_graph, sentence_transformers; print("CORE_IMPORTS=ok")'

$required = @(
    'shibing624\text2vec-base-chinese\config.json',
    'shibing624\text2vec-base-chinese\pytorch_model.bin'
)
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $ModelRoot $_)) })
if ($missing.Count) {
    throw "必需模型文件缺失：$($missing -join '、')"
}
if (Test-Path -LiteralPath $AsrPython) {
    $PSNativeCommandUseErrorActionPreference = $false
    $AsrCheck = & $AsrPython -c 'import funasr, torch; assert torch.cuda.is_available(); print("ASR_CUDA=ok", torch.__version__, funasr.__version__)' 2>&1
    $AsrCheckCode = $LASTEXITCODE
    $PSNativeCommandUseErrorActionPreference = $true
    if ($AsrCheckCode -eq 0) {
        $AsrCheck | Write-Output
    }
    else {
        Write-Warning '检测到未完成的 ASR CUDA 环境；基础文字功能正常，请在启动器点击“继续修复并启动”。'
    }
}
else {
    Write-Warning 'ASR CUDA 运行时未安装；基础文字、RAG 与云端语音功能不受影响。'
}
$settings = Get-Content -Raw -LiteralPath (Join-Path $RuntimeRoot 'config\settings.json') | ConvertFrom-Json
if ($settings.llm.mode -eq 'openai' -and -not $settings.llm.api_key) {
    throw 'LLM 已选择真实 API，但尚未配置密钥。'
}
if ($settings.audio.tts_provider -eq 'siliconflow' -and -not $settings.audio.tts_siliconflow_api_key) {
    Write-Warning 'SiliconFlow TTS 尚未配置密钥；文字对话和 ASR 不受影响。'
}
if ($settings.audio.tts_provider -eq 'cosyvoice') {
    $TtsMarkerRoot = if ($env:MINDSPACE_TTS_MARKER_ROOT) { $env:MINDSPACE_TTS_MARKER_ROOT } else { Join-Path $ProjectRoot 'runtime\components\tts-runtime' }
    $TtsMarker = Join-Path $TtsMarkerRoot 'ready.json'
    $TtsModel = Join-Path $ModelRoot 'tts\Fun-CosyVoice3-0.5B-2512\cosyvoice3.yaml'
    $TtsReference = [string]$settings.audio.tts_reference_audio
    if (-not (Test-Path -LiteralPath $AsrPython)) {
        throw 'CosyVoice 需要共享 CUDA 语音运行时，请先安装本地语音组件。'
    }
    if (-not (Test-Path -LiteralPath $TtsMarker)) {
        throw 'CosyVoice 运行时缺失，请在组件区安装“CosyVoice 运行时”。'
    }
    if (-not (Test-Path -LiteralPath $TtsModel)) {
        throw 'CosyVoice 模型缺失，请在组件区下载“本地 CosyVoice 3”。'
    }
    if (-not $TtsReference -or -not (Test-Path -LiteralPath $TtsReference)) {
        throw 'CosyVoice 参考音频缺失，请先在声音设置中上传。'
    }
    & $AsrPython -c 'import pathlib, sys, torch; root=pathlib.Path.cwd(); sys.path.insert(0, str(root / "vendor" / "CosyVoice")); sys.path.insert(0, str(root / "vendor" / "CosyVoice" / "third_party" / "Matcha-TTS")); from cosyvoice.cli.cosyvoice import AutoModel; assert torch.cuda.is_available(); print("TTS_CUDA=ok")'
}
if ($settings.audio.tts_provider -eq 'gpt-sovits') {
    $GptVenv = if ($env:MINDSPACE_GPT_SOVITS_VENV) { $env:MINDSPACE_GPT_SOVITS_VENV } else { Join-Path $ProjectRoot '.venv-gpt-sovits' }
    $GptPython = Join-Path $GptVenv 'Scripts\python.exe'
    $GptMarker = Join-Path $GptVenv 'ready.json'
    $GptCode = if ($env:MINDSPACE_GPT_SOVITS_CODE_ROOT) { $env:MINDSPACE_GPT_SOVITS_CODE_ROOT } else { Join-Path $ProjectRoot 'vendor\GPT-SoVITS' }
    $GptRuntime = if ($env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT) { $env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT } else { Join-Path $ModelRoot 'tts\gpt-sovits\runtime' }
    $CatalogPath = Join-Path $ProjectRoot 'config\gpt-sovits-voices.json'
    foreach ($requiredPath in $GptPython, $GptMarker, $CatalogPath, (Join-Path $GptCode 'GPT_SoVITS\TTS_infer_pack\TTS.py'), (Join-Path $GptRuntime 'GPT_SoVITS\pretrained_models\s1v3.ckpt')) {
        if (-not (Test-Path -LiteralPath $requiredPath)) { throw "GPT-SoVITS 运行时缺失：$requiredPath" }
    }
    $Catalog = Get-Content -Raw -LiteralPath $CatalogPath | ConvertFrom-Json
    $VoiceId = if ($settings.audio.tts_gpt_sovits_voice) { [string]$settings.audio.tts_gpt_sovits_voice } else { 'v4-changli' }
    $Voice = $Catalog.voices | Where-Object id -eq $VoiceId | Select-Object -First 1
    if (-not $Voice) { throw "未知 GPT-SoVITS 音色：$VoiceId" }
    $VoiceRoot = Join-Path $ModelRoot ([string]$Voice.directory)
    foreach ($voiceFile in ([string]$Voice.gpt_weight), ([string]$Voice.sovits_weight), ([string]$Voice.reference_audio)) {
        if (-not (Test-Path -LiteralPath (Join-Path $VoiceRoot $voiceFile))) { throw "GPT-SoVITS 音色不完整：$VoiceId / $voiceFile" }
    }
    $env:MINDSPACE_GPT_SOVITS_CODE_ROOT = $GptCode
    $env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT = $GptRuntime
    & $GptPython -c 'import os,pathlib,sys; code=pathlib.Path(os.environ["MINDSPACE_GPT_SOVITS_CODE_ROOT"]); runtime=pathlib.Path(os.environ["MINDSPACE_GPT_SOVITS_RUNTIME_ROOT"]); os.chdir(runtime); sys.path[:0]=[str(code/"GPT_SoVITS"),str(code)]; import torch,torchaudio; from GPT_SoVITS.TTS_infer_pack.TTS import TTS,TTS_Config; assert torch.cuda.is_available(); print("GPT_SOVITS_CUDA=ok", torch.__version__)'
}

Write-Output 'MINDSPACE_RUNTIME=ready'
