[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$AsrVenv = if ($env:MINDSPACE_ASR_VENV) { $env:MINDSPACE_ASR_VENV } else { Join-Path $ProjectRoot '.venv-asr' }
$PythonExe = Join-Path $AsrVenv 'Scripts\python.exe'
$Requirements = Join-Path $PSScriptRoot 'requirements-tts-runtime.txt'
$CosyVoiceRoot = Join-Path $ProjectRoot 'vendor\CosyVoice'
$MarkerRoot = if ($env:MINDSPACE_TTS_MARKER_ROOT) { $env:MINDSPACE_TTS_MARKER_ROOT } else { Join-Path $ProjectRoot 'runtime\components\tts-runtime' }
$Marker = Join-Path $MarkerRoot 'ready.json'
$UvExe = if ($env:MINDSPACE_UV) { $env:MINDSPACE_UV } else { (Get-Command uv -ErrorAction Stop).Source }
$DomesticIndex = 'https://mirrors.aliyun.com/pypi/simple/'
$OfficialIndex = 'https://pypi.org/simple/'
$UseOfficialSource = $env:MINDSPACE_DOWNLOAD_SOURCE -eq 'official'
$PackageIndex = if ($UseOfficialSource) { $OfficialIndex } else { $DomesticIndex }
$SourceLabel = if ($UseOfficialSource) { '官方源' } else { '国内镜像' }
$TorchSourceArguments = if ($UseOfficialSource) {
    @('--index-url', 'https://download.pytorch.org/whl/cu128', '--extra-index-url', $OfficialIndex)
} else {
    @('--index-url', $DomesticIndex, '--find-links', 'https://mirrors.aliyun.com/pytorch-wheels/cu128/')
}
$env:UV_DEFAULT_INDEX = $PackageIndex
$env:PIP_INDEX_URL = $PackageIndex
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw '共享语音运行时缺失，请先安装“ASR CUDA 运行时”。'
}
if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "TTS 依赖清单缺失：$Requirements"
}
if (-not (Test-Path -LiteralPath (Join-Path $CosyVoiceRoot 'cosyvoice\cli\cosyvoice.py'))) {
    throw "CosyVoice 运行代码缺失：$CosyVoiceRoot"
}

$VerifyCode = @'
import pathlib, sys
root = pathlib.Path.cwd()
sys.path.insert(0, str(root / "vendor" / "CosyVoice"))
sys.path.insert(0, str(root / "vendor" / "CosyVoice" / "third_party" / "Matcha-TTS"))
import torch, torchaudio, soundfile, onnxruntime, hyperpyyaml, transformers, wetext
from cosyvoice.cli.cosyvoice import AutoModel
assert torch.cuda.is_available(), "CUDA is unavailable"
print("TTS_IMPORTS=ok", torch.__version__)
'@

Set-Location $ProjectRoot
Write-Output 'TTS_STAGE=preflight'
$PSNativeCommandUseErrorActionPreference = $false
& $PythonExe -c $VerifyCode *> $null
$Ready = $LASTEXITCODE -eq 0
$PSNativeCommandUseErrorActionPreference = $true
if (-not $Ready) {
    # openai-whisper 20231117 imports pkg_resources during its legacy build
    # without declaring setuptools. Prepare it in the shared environment and
    # disable build isolation only for that legacy package.
    Write-Output 'TTS_STAGE=build-tools'
    & $UvExe pip install --python $PythonExe 'setuptools<81' wheel --index-url $PackageIndex
    if ($LASTEXITCODE -ne 0) { throw 'TTS 构建工具安装失败。' }

    # The ASR and TTS runtimes intentionally share the same CUDA environment.
    # Pinning the pair prevents a transitive dependency from replacing it with
    # a CPU-only PyTorch wheel.
    Write-Output 'TTS_STAGE=torch'
    & $UvExe pip install --python $PythonExe 'torch==2.11.0+cu128' 'torchaudio==2.11.0+cu128' `
        @TorchSourceArguments
    if ($LASTEXITCODE -ne 0) { throw 'TTS 所需 CUDA 版 PyTorch 安装失败。' }

    Write-Output 'TTS_STAGE=dependencies'
    & $UvExe pip install --python $PythonExe -r $Requirements --index-url $PackageIndex --no-build-isolation-package openai-whisper
    if ($LASTEXITCODE -ne 0) { throw "CosyVoice 运行依赖从$SourceLabel安装失败。" }
} else {
    Write-Output 'TTS_STAGE=reuse'
}

Write-Output 'TTS_STAGE=verify'
& $PythonExe -c $VerifyCode

Write-Output 'TTS_STAGE=marker'
New-Item -ItemType Directory -Path $MarkerRoot -Force | Out-Null
@{
    schema_version = '1.0.0'
    installed_at = (Get-Date).ToUniversalTime().ToString('o')
    python = $PythonExe
    shared_with_asr = $true
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Marker -Encoding utf8

Write-Output "TTS_PYTHON=$PythonExe"
Write-Output "TTS_MARKER=$Marker"
Write-Output 'TTS_STAGE=done'
