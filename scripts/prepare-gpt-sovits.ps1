[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$AsrVenv = if ($env:MINDSPACE_ASR_VENV) { $env:MINDSPACE_ASR_VENV } else { Join-Path $ProjectRoot '.venv-asr' }
$GptVenv = if ($env:MINDSPACE_GPT_SOVITS_VENV) { $env:MINDSPACE_GPT_SOVITS_VENV } else { Join-Path $ProjectRoot '.venv-gpt-sovits' }
$CodeRoot = if ($env:MINDSPACE_GPT_SOVITS_CODE_ROOT) { $env:MINDSPACE_GPT_SOVITS_CODE_ROOT } else { Join-Path $ProjectRoot 'vendor\GPT-SoVITS' }
$RuntimeRoot = if ($env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT) { $env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT } else { Join-Path $ProjectRoot 'assets\models\tts\gpt-sovits\runtime' }
$Requirements = Join-Path $PSScriptRoot 'requirements-gpt-sovits-runtime.txt'
$Excludes = Join-Path $PSScriptRoot 'excludes-gpt-sovits-runtime.txt'
$AsrPython = Join-Path $AsrVenv 'Scripts\python.exe'
$GptPython = Join-Path $GptVenv 'Scripts\python.exe'
$Marker = Join-Path $GptVenv 'ready.json'
$Ffmpeg = if ($env:MINDSPACE_FFMPEG) { $env:MINDSPACE_FFMPEG } else { Join-Path $ProjectRoot '.tools\ffmpeg\8.1.2\ffmpeg.exe' }
$UvExe = if ($env:MINDSPACE_UV) { $env:MINDSPACE_UV } else { (Get-Command uv -ErrorAction Stop).Source }
$DomesticIndex = 'https://mirrors.aliyun.com/pypi/simple/'
$OfficialIndex = 'https://pypi.org/simple/'
$UseOfficialSource = $env:MINDSPACE_DOWNLOAD_SOURCE -eq 'official'
$PackageIndex = if ($UseOfficialSource) { $OfficialIndex } else { $DomesticIndex }
$SourceLabel = if ($UseOfficialSource) { '官方源' } else { '国内镜像' }
$env:UV_DEFAULT_INDEX = $PackageIndex
$env:PIP_INDEX_URL = $PackageIndex
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

Write-Output 'GPT_SOVITS_STAGE=preflight'
foreach ($required in $AsrPython, $Ffmpeg, $Requirements, $Excludes, (Join-Path $CodeRoot 'GPT_SoVITS\TTS_infer_pack\TTS.py'), (Join-Path $RuntimeRoot 'GPT_SoVITS\pretrained_models\s1v3.ckpt')) {
    if (-not (Test-Path -LiteralPath $required)) { throw "GPT-SoVITS prerequisite is missing: $required" }
}
& $AsrPython -c "import torch, torchaudio; assert torch.cuda.is_available(); assert torch.version.cuda; print(torch.__version__)"
& $Ffmpeg -version *> $null
$env:PATH = "$(Split-Path -Parent $Ffmpeg);$env:PATH"

Write-Output 'GPT_SOVITS_STAGE=venv'
if (-not (Test-Path -LiteralPath $GptPython)) {
    $PrivatePython = if ($env:UV_PYTHON_INSTALL_DIR) {
        Get-ChildItem -LiteralPath $env:UV_PYTHON_INSTALL_DIR -Filter python.exe -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    }
    $ClearArguments = @()
    if (Test-Path -LiteralPath $GptVenv) {
        Write-Output 'GPT_SOVITS_VENV_RECOVERY=clear'
        $ClearArguments = @('--clear')
    }
    if ($PrivatePython) { & $UvExe venv --seed @ClearArguments $GptVenv --python $PrivatePython }
    else { & $UvExe venv --seed @ClearArguments $GptVenv --python '3.11' }
    if ($LASTEXITCODE -ne 0) { throw 'GPT-SoVITS 独立 Python 环境创建失败。' }
}

$SiteCode = 'import sysconfig; print(sysconfig.get_paths()["purelib"])'
$AsrSite = (& $AsrPython -c $SiteCode).Trim()
$GptSite = (& $GptPython -c $SiteCode).Trim()
if (-not (Test-Path -LiteralPath $AsrSite) -or -not (Test-Path -LiteralPath $GptSite)) { throw '无法定位 Python site-packages。' }

Write-Output 'GPT_SOVITS_STAGE=torch'
$TorchNames = @('torch', 'torchaudio', 'torchvision', 'torchgen', 'functorch')
$TorchQuarantine = Join-Path $GptVenv '.torch-quarantine'
function Move-LocalTorchAside([string]$Reason) {
    $Candidates = Get-ChildItem -LiteralPath $GptSite -Force | Where-Object {
        $_.Name -in $TorchNames -or $_.Name -match '^(torch|torchaudio|torchvision)-[0-9].*\.dist-info$'
    }
    if (-not $Candidates) { return }
    New-Item -ItemType Directory -Path $TorchQuarantine -Force | Out-Null
    $Stamp = [DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')
    foreach ($Entry in $Candidates) {
        Move-Item -LiteralPath $Entry.FullName -Destination (Join-Path $TorchQuarantine "$Reason-$Stamp-$($Entry.Name)")
    }
}
$LegacyPathFile = Join-Path $GptSite '_mindspace_asr_cuda.pth'
if (Test-Path -LiteralPath $LegacyPathFile) { Move-Item -LiteralPath $LegacyPathFile -Destination "$LegacyPathFile.disabled" -Force }
Move-LocalTorchAside 'before-dependencies'

Write-Output 'GPT_SOVITS_STAGE=dependencies'
& $UvExe pip install --python $GptPython -r $Requirements --excludes $Excludes --index-url $PackageIndex --index-strategy unsafe-best-match
if ($LASTEXITCODE -ne 0) { throw "GPT-SoVITS 推理依赖从$SourceLabel安装失败。" }
Move-LocalTorchAside 'after-dependencies'
$TorchEntries = Get-ChildItem -LiteralPath $AsrSite -Force | Where-Object {
    $_.Name -in $TorchNames -or $_.Name -match '^(torch|torchaudio|torchvision)-.*\.dist-info$'
}
foreach ($Entry in $TorchEntries) {
    New-Item -ItemType Junction -Path (Join-Path $GptSite $Entry.Name) -Target $Entry.FullName | Out-Null
}

Write-Output 'GPT_SOVITS_STAGE=project'
$VerifyCode = @'
import os, pathlib, sys
code = pathlib.Path(os.environ["MINDSPACE_GPT_SOVITS_CODE_ROOT"])
runtime = pathlib.Path(os.environ["MINDSPACE_GPT_SOVITS_RUNTIME_ROOT"])
os.chdir(runtime)
sys.path.insert(0, str(code))
sys.path.insert(0, str(code / "GPT_SoVITS"))
sys.path.insert(0, str(code / "GPT_SoVITS" / "eres2net"))
import torch, torchaudio, soundfile, transformers
from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
assert torch.cuda.is_available(), "CUDA is unavailable"
asr_venv = pathlib.Path(os.environ["MINDSPACE_ASR_VENV"]).resolve()
assert pathlib.Path(torch.__file__).resolve().is_relative_to(asr_venv), "Torch must be loaded from the verified ASR CUDA environment"
assert tuple(int(v) for v in transformers.__version__.split(".")[:2]) <= (4, 50)
assert (runtime / "GPT_SoVITS" / "text" / "G2PWModel" / "g2pW.onnx").is_file()
print("GPT_SOVITS_IMPORTS=ok", torch.__version__, transformers.__version__)
'@

Write-Output 'GPT_SOVITS_STAGE=verify'
$env:MINDSPACE_GPT_SOVITS_CODE_ROOT = $CodeRoot
$env:MINDSPACE_GPT_SOVITS_RUNTIME_ROOT = $RuntimeRoot
& $GptPython -c $VerifyCode

Write-Output 'GPT_SOVITS_STAGE=marker'
@{
    schema_version = '1.0.0'
    ready = $true
    installed_at = [DateTime]::UtcNow.ToString('o')
    python = $GptPython
    torch_source = $AsrSite
    isolated_dependencies = $true
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath "$Marker.next" -Encoding utf8
Move-Item -LiteralPath "$Marker.next" -Destination $Marker -Force

Write-Output "GPT_SOVITS_PYTHON=$GptPython"
Write-Output "GPT_SOVITS_MARKER=$Marker"
Write-Output 'GPT_SOVITS_STAGE=done'
