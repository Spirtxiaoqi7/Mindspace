[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'service-ports.ps1')
$QwenPort = (Get-MindspaceServicePorts -ProjectRoot $ProjectRoot).qwen

function Test-QwenHealth {
  try {
    return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:$QwenPort/health").StatusCode -eq 200
  } catch { return $false }
}

if (Test-QwenHealth) { exit 0 }

$occupied = Get-NetTCPConnection -State Listen -LocalPort $QwenPort -ErrorAction SilentlyContinue | Select-Object -First 1
if ($occupied) {
  throw "端口 $QwenPort 已被未知进程 $($occupied.OwningProcess) 占用，但不是可用的 Qwen3 服务；Mindspace 未终止该进程。"
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
& $wsl.Source --distribution $distro -- env "MINDSPACE_QWEN3_PORT=$QwenPort" bash $launcher
exit $LASTEXITCODE
