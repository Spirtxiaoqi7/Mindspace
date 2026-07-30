[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Test-QwenHealth {
  try {
    return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:8091/health").StatusCode -eq 200
  } catch { return $false }
}

if (Test-QwenHealth) { exit 0 }

$occupied = Get-NetTCPConnection -State Listen -LocalPort 8091 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($occupied) {
  throw "端口 8091 已被进程 $($occupied.OwningProcess) 占用，但不是可用的 Qwen3 服务。请在诊断页释放端口后重试。"
}

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) { throw "WSL2 不可用；请在 Launcher 组件区修复 Qwen3 实时语音运行时。" }
$runtimeRoot = if ($env:MINDSPACE_QWEN3_RUNTIME_ROOT) { $env:MINDSPACE_QWEN3_RUNTIME_ROOT } else { Join-Path $env:MINDSPACE_HOME "environment\qwen3-vllm" }
$markerPath = Join-Path $runtimeRoot "ready.json"
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw "Qwen3 运行时未完成安装，请先在 Launcher 组件区安装。" }
$marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
$distro = if ($marker.distro) { [string]$marker.distro } elseif ($env:MINDSPACE_QWEN3_WSL_DISTRO) { $env:MINDSPACE_QWEN3_WSL_DISTRO } else { "MindspaceVLLM" }
$launcher = [string]$marker.launcher_wsl_path
if (-not $launcher) { throw "Qwen3 启动配置损坏；请在 Launcher 组件区执行修复。" }

# This process intentionally remains attached to the launcher. Its managed
# WSL wrapper performs one invisible warm-up while Core, ASR and the product
# window continue starting independently.
& $wsl.Source --distribution $distro -- bash $launcher
exit $LASTEXITCODE
