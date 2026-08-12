[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'service-ports.ps1')
$DefaultQwenPort = (Get-MindspaceServicePorts -ProjectRoot $ProjectRoot).qwen

function Write-Stage([string]$Name) { Write-Output "QWEN3_STAGE=$Name" }

Write-Stage "preflight"
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
  throw "未检测到 WSL2。请在 Windows 功能中启用 WSL2，并重启后重新安装 Qwen3 实时语音运行时。"
}

$distro = if ($env:MINDSPACE_QWEN3_WSL_DISTRO) { $env:MINDSPACE_QWEN3_WSL_DISTRO } else { "MindspaceVLLM" }
$installed = @(& $wsl.Source --list --quiet 2>$null | ForEach-Object { $_.Trim() })
if ($installed -notcontains $distro) {
  throw "未找到受管 WSL 发行版 $distro。请先在 Launcher 的修复提示中安装 Qwen3 WSL2 运行时，再重试。"
}

function Convert-ToWslPath([string]$WindowsPath) {
  # wslpath receives arguments without Windows shell parsing. Normalize the
  # drive form ourselves so backslashes are never consumed as escapes.
  $normalized = $WindowsPath.Replace("\", "/")
  $value = (& $wsl.Source --distribution $distro -- wslpath -a $normalized 2>$null | Select-Object -First 1)
  if ($LASTEXITCODE -ne 0 -or -not $value) { return "" }
  return ([string]$value).Trim()
}

Write-Stage "gpu"
& $wsl.Source --distribution $distro -- nvidia-smi -L 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "WSL2 内没有可用 NVIDIA GPU。请更新 Windows NVIDIA 驱动并确认 'wsl -d $distro nvidia-smi' 可用。"
}

$runtimeRoot = if ($env:MINDSPACE_QWEN3_RUNTIME_ROOT) { $env:MINDSPACE_QWEN3_RUNTIME_ROOT } else { Join-Path $env:MINDSPACE_HOME "environment\qwen3-vllm" }
$candidates = @(@(
  $env:MINDSPACE_QWEN3_WSL_LAUNCHER,
  # Always inspect the original launcher first. The managed runtime wrapper
  # intentionally has no MODEL= line and is only for keeping vLLM alive.
  (Join-Path $env:MINDSPACE_HOME "experimental\vllm-omni\start-qwen3-tts.sh"),
  (Join-Path $runtimeRoot "start-qwen3-tts.sh")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) })
if (-not $candidates.Count) {
  throw "Qwen3 启动脚本或模型不完整。请重新安装 Qwen3 实时语音运行时；不会修改 ASR、GPT-SoVITS 或用户数据。"
}

$launcherPath = $candidates[0]
Write-Stage "verify"
$launcherText = Get-Content -LiteralPath $launcherPath -Raw
$modelMatch = [regex]::Match($launcherText, '(?m)^MODEL=(.+)$')
if (-not $modelMatch.Success) {
  throw "无法从 Qwen3 启动脚本读取 MODEL 路径；请在组件区修复运行时。"
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$linuxRuntimeRoot = Convert-ToWslPath $runtimeRoot
if (-not $linuxRuntimeRoot) {
  throw "无法创建 Qwen3 的受管 WSL2 运行目录。请检查运行目录权限。"
}

Write-Stage "model"
$modelPath = Join-Path $env:MINDSPACE_HOME "experimental\qwen3-tts\models\Qwen3-TTS-12Hz-1.7B-CustomVoice"
$modelWeight = Join-Path $modelPath "model.safetensors"
if (-not (Test-Path -LiteralPath (Join-Path $modelPath "config.json")) -or
  -not (Test-Path -LiteralPath $modelWeight) -or
  (Get-Item -LiteralPath $modelWeight).Length -lt 3GB) {
  throw "Qwen3 CustomVoice 模型不完整。不会回退到 Base 背板声线或自动下载其他模型。"
}
$linuxModelPath = Convert-ToWslPath $modelPath
$deployMatch = [regex]::Match($launcherText, '(?m)^DEPLOY_CONFIG=(.+)$')
if (-not $linuxModelPath -or -not $deployMatch.Success) {
  throw "Qwen3 CustomVoice 运行配置不完整；请在组件区执行修复。"
}
$linuxDeployPath = $deployMatch.Groups[1].Value.Trim()
# The checked-in launcher deliberately derives DEPLOY_CONFIG from VENV.
# `wsl.exe -- test -f` does not invoke a shell, so expand that one known
# launcher variable before validating the path.  Do not use Invoke-Expression
# or a shell here: the launcher is local configuration, not executable input.
if ($linuxDeployPath.StartsWith('$VENV/')) {
  $venvMatch = [regex]::Match($launcherText, '(?m)^VENV=(.+)$')
  if (-not $venvMatch.Success) {
    throw "Qwen3 启动脚本使用了 VENV，但没有声明其路径；请修复受管运行时。"
  }
  $linuxDeployPath = $venvMatch.Groups[1].Value.TrimEnd('/') + $linuxDeployPath.Substring(5)
}
& $wsl.Source --distribution $distro -- test -f $linuxDeployPath
if ($LASTEXITCODE -ne 0) {
  throw "Qwen3 CustomVoice 的 vLLM 部署配置缺失；请修复受管运行时。"
}
$enginePath = Join-Path $runtimeRoot "engine-qwen3-tts.sh"
$engineText = [regex]::Replace(
  $launcherText,
  '(?m)^MODEL=.+$',
  "MODEL=$linuxModelPath"
)
$engineText = $engineText.Replace([string]$DefaultQwenPort, '${MINDSPACE_QWEN3_PORT}')
$engineText = $engineText -replace "`r?`n", "`n"
[System.IO.File]::WriteAllText(
  $enginePath,
  $engineText,
  [System.Text.UTF8Encoding]::new($false)
)
$linuxLauncher = Convert-ToWslPath $enginePath
& $wsl.Source --distribution $distro -- chmod +x $linuxLauncher
if ($LASTEXITCODE -ne 0) { throw "无法标记 Qwen3 CustomVoice 引擎脚本为可执行。" }
if ($linuxLauncher.Contains("'") -or $linuxRuntimeRoot.Contains("'")) {
  throw "Qwen3 运行路径不能包含单引号。请将运行目录迁移到常规路径后重试。"
}
$escapedLauncher = $linuxLauncher
$escapedRuntime = $linuxRuntimeRoot
$wrapperPath = Join-Path $runtimeRoot "start-qwen3-tts.sh"
$ownerPath = Join-Path $runtimeRoot "qwen3-vllm.owner"
$ownerToken = [Guid]::NewGuid().ToString('N')
Set-Content -LiteralPath $ownerPath -Value $ownerToken -Encoding ascii
$wrapper = @'
#!/usr/bin/env bash
set -euo pipefail
source_launcher='__SOURCE_LAUNCHER__'
runtime_root='__RUNTIME_ROOT__'
qwen_port="${MINDSPACE_QWEN3_PORT:?MINDSPACE_QWEN3_PORT is required}"
pid_path="$runtime_root/qwen3-vllm.pid"
owner_path="$runtime_root/qwen3-vllm.owner"
lock_path="$runtime_root/qwen3-vllm.lock"
exec 9>"$lock_path"
# A Launcher restart while vLLM is still compiling must never start a second
# engine on the same GPU. A second wrapper waits for the owner instead of
# competing for the registered Qwen port or resetting the model-load state.
if ! flock -n 9; then
  echo 'Qwen3 supervisor already owns this runtime; waiting for it to exit.'
  flock 9
  exit 0
fi
server_pid=''
cleanup() {
  if [ -n "$server_pid" ] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -f "$pid_path"
}
trap cleanup EXIT INT TERM
export MINDSPACE_QWEN_OWNER="$(tr -d '\r\n' < "$owner_path")"
"$source_launcher" &
server_pid=$!
printf '%s\n' "$server_pid" > "$pid_path"
for _ in $(seq 1 600); do
  if curl --fail --silent --show-error "http://127.0.0.1:$qwen_port/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo 'Qwen3 engine exited before its health endpoint became ready.' >&2
    exit 1
  fi
  sleep 1
done
if ! curl --fail --silent --show-error "http://127.0.0.1:$qwen_port/health" >/dev/null 2>&1; then
  echo 'Qwen3 health endpoint did not become ready' >&2
  exit 1
fi
echo 'Qwen3 warm-up started'
curl --fail --silent --show-error --max-time 180 \
  -H 'Content-Type: application/json' \
  -d '{"model":"mindspace-qwen3-tts","input":"嗯……我在。","voice":"serena","instructions":"语速舒缓，像熟悉伴侣近距离聊天，句间自然换气。","response_format":"pcm","stream":false,"task_type":"CustomVoice","language":"Chinese","non_streaming_mode":true,"max_new_tokens":128}' \
  "http://127.0.0.1:$qwen_port/v1/audio/speech" >/dev/null
date -u +%FT%TZ > "$runtime_root/warmup.ready"
echo 'Qwen3 warm-up completed'
set +e
wait "$server_pid"
server_status=$?
set -e
exit "$server_status"
'@
$normalizedWrapper = $wrapper.Replace("__SOURCE_LAUNCHER__", $escapedLauncher).Replace("__RUNTIME_ROOT__", $escapedRuntime) -replace "`r?`n", "`n"
# The wrapper is executed by bash in WSL. Windows PowerShell's default
# Set-Content line endings append CR to `wait "$server_pid"`, which makes the
# child PID invalid after a successful warm-up and immediately tears vLLM down.
[System.IO.File]::WriteAllText(
  $wrapperPath,
  $normalizedWrapper,
  [System.Text.UTF8Encoding]::new($false)
)
$linuxWrapper = Convert-ToWslPath $wrapperPath
& $wsl.Source --distribution $distro -- chmod +x $linuxWrapper
if ($LASTEXITCODE -ne 0) { throw "无法标记 Qwen3 受管启动脚本为可执行。" }
# A previous provider's warm-up marker must never make the restored
# CustomVoice engine look ready before Serena and its style control are loaded.
$warmupMarker = Join-Path $runtimeRoot "warmup.ready"
if (Test-Path -LiteralPath $warmupMarker) {
  Remove-Item -LiteralPath $warmupMarker -Force
}
$marker = @{
  ready = $true
  provider = "qwen3-vllm"
  distro = $distro
  launcher_wsl_path = $linuxWrapper
  source_launcher_wsl_path = $linuxLauncher
  verified_at = [DateTime]::UtcNow.ToString("o")
  notes = "Qwen3 在首次后台启动时会编译和预热；主界面、ASR 和文字聊天不会等待。"
}
$temporary = Join-Path $runtimeRoot ("ready-{0}.json" -f [Guid]::NewGuid().ToString("n"))
$marker | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding utf8
Move-Item -LiteralPath $temporary -Destination (Join-Path $runtimeRoot "ready.json") -Force
Write-Stage "done"
