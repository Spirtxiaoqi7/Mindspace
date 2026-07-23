[CmdletBinding()]
param(
    [string]$Python = '3.11',
    [switch]$Rebuild,
    [switch]$SkipModels
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvRoot = if ($env:MINDSPACE_ASR_VENV) { $env:MINDSPACE_ASR_VENV } else { Join-Path $ProjectRoot '.venv-asr' }
$SeedRoot = if ($env:MINDSPACE_TTS_VENV) { $env:MINDSPACE_TTS_VENV } else { Join-Path $ProjectRoot '.venv-tts' }
$PythonExe = Join-Path $VenvRoot 'Scripts\python.exe'
$ReadyMarker = Join-Path $VenvRoot '.mindspace-asr-ready.json'
$ModelRoot = if ($env:MINDSPACE_MODEL_ROOT) { Join-Path $env:MINDSPACE_MODEL_ROOT 'asr' } else { Join-Path $ProjectRoot 'assets\models\asr' }
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

Set-Location $ProjectRoot
Write-Output 'ASR_STAGE=venv'
if (Test-Path -LiteralPath $ReadyMarker) {
    Remove-Item -LiteralPath $ReadyMarker -Force
}
if ($Rebuild -and (Test-Path -LiteralPath $VenvRoot)) {
    $resolvedTarget = (Resolve-Path -LiteralPath $VenvRoot).Path
    $AllowedEnvironmentRoot = if ($env:MINDSPACE_ENVIRONMENT) { [IO.Path]::GetFullPath($env:MINDSPACE_ENVIRONMENT).TrimEnd('\') } else { $ProjectRoot }
    if (-not $resolvedTarget.StartsWith($AllowedEnvironmentRoot, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Leaf $resolvedTarget) -notin @('.venv-asr', 'asr-cuda')) {
        throw "Refusing to rebuild unsafe ASR environment path: $resolvedTarget"
    }
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    if (Test-Path -LiteralPath (Join-Path $SeedRoot 'Scripts\python.exe')) {
        New-Item -ItemType Directory -Force -Path $VenvRoot | Out-Null
        $PSNativeCommandUseErrorActionPreference = $false
        robocopy $SeedRoot $VenvRoot /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
        $copyCode = $LASTEXITCODE
        $PSNativeCommandUseErrorActionPreference = $true
        if ($copyCode -ge 8) {
            throw "ASR CUDA seed copy failed with robocopy code $copyCode"
        }
    }
    else {
        & $UvExe venv $VenvRoot --python $Python
    }
}

# A cancelled installation can leave python.exe behind without a usable CUDA
# stack. Probe the actual imports instead of treating the directory as ready.
$PSNativeCommandUseErrorActionPreference = $false
& $PythonExe -c "import torch, torchaudio; assert torch.cuda.is_available(); assert torch.version.cuda" *> $null
$TorchReady = $LASTEXITCODE -eq 0
$PSNativeCommandUseErrorActionPreference = $true
if (-not $TorchReady) {
    Write-Output 'ASR_STAGE=torch'
    & $UvExe pip install --python $PythonExe 'torch==2.11.0+cu128' 'torchaudio==2.11.0+cu128' `
        @TorchSourceArguments
    if ($LASTEXITCODE -ne 0) {
        throw "CUDA 版 PyTorch 从$SourceLabel安装失败。"
    }
}
Write-Output 'ASR_STAGE=funasr'
$BuildPackages = @('setuptools<81', 'wheel')
& $UvExe pip install --python $PythonExe @BuildPackages --index-url $PackageIndex
if ($LASTEXITCODE -ne 0) { throw "ASR 构建基础依赖从$SourceLabel安装失败。" }
$SpeechPackages = @('funasr>=1.3.15,<2', 'tiktoken>=0.9,<1', 'huggingface-hub>=0.34,<2', 'fastapi>=0.115,<1', 'uvicorn>=0.34,<1', 'websockets>=15,<17')
& $UvExe pip install --python $PythonExe @SpeechPackages --index-url $PackageIndex
if ($LASTEXITCODE -ne 0) { throw "FunASR 运行依赖从$SourceLabel安装失败。" }
Write-Output 'ASR_STAGE=project'
& $UvExe pip install --python $PythonExe --no-deps -e .
Write-Output 'ASR_STAGE=verify'
& $PythonExe -c "import torch, torchaudio, funasr, fastapi, uvicorn, websockets; assert torch.cuda.is_available(); print(torch.__version__, funasr.__version__)"

if (-not $SkipModels) {
    New-Item -ItemType Directory -Force -Path $ModelRoot | Out-Null
    & $PythonExe (Join-Path $PSScriptRoot 'download-asr-models.py') --output $ModelRoot
}

Write-Output "ASR_PYTHON=$PythonExe"
Write-Output "ASR_MODELS=$ModelRoot"
@{
    schema_version = '1.1.0'
    ready = $true
    final_refinement = 'Fun-ASR-Nano-2512'
    python = $PythonExe
    verified_at = [DateTime]::UtcNow.ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath "$ReadyMarker.next" -Encoding utf8
Move-Item -LiteralPath "$ReadyMarker.next" -Destination $ReadyMarker -Force
Write-Output 'ASR_STAGE=done'
