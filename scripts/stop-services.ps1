[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$IdentityRoot,
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string[]]$Services = @('api', 'asr', 'tts'),
    [switch]$IncludeQwen,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'service-ports.ps1')
$Ports = Get-MindspaceServicePorts -ProjectRoot $ProjectRoot
if (-not $IdentityRoot) {
    if (-not $env:MINDSPACE_SERVICE_IDENTITY_ROOT) { throw 'Mindspace service identity root was not provided' }
    $IdentityRoot = $env:MINDSPACE_SERVICE_IDENTITY_ROOT
}
$IdentityRoot = [IO.Path]::GetFullPath($IdentityRoot)
if ($IncludeQwen -and $Services -notcontains 'qwenTts') { $Services += 'qwenTts' }
$PortByService = @{ api = $Ports.core; asr = $Ports.asr; tts = $Ports.tts; qwenTts = $Ports.qwen }
$stopped = @()
$conflicts = @()

foreach ($name in $Services | Select-Object -Unique) {
    if (-not $PortByService.ContainsKey($name)) { throw "Unknown Mindspace service: $name" }
    $identityPath = Join-Path $IdentityRoot "$name.json"
    $listener = Get-NetTCPConnection -State Listen -LocalPort $PortByService[$name] -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not (Test-Path -LiteralPath $identityPath -PathType Leaf)) {
        if ($listener) { $conflicts += @{ service = $name; port = $PortByService[$name]; pid = $listener.OwningProcess; reason = 'unknown-owner' } }
        continue
    }
    try { $identity = Get-Content -LiteralPath $identityPath -Raw | ConvertFrom-Json } catch {
        $conflicts += @{ service = $name; port = $PortByService[$name]; pid = 0; reason = 'invalid-identity' }
        continue
    }
    $processId = [int]$identity.pid
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    $expectedScript = [IO.Path]::GetFullPath([string]$identity.script)
    $expectedExecutable = [IO.Path]::GetFullPath([string]$identity.executable)
    $commandLine = [string]$process.CommandLine
    $executablePath = [string]$process.ExecutablePath
    $identityMatches = $process -and
        [string]$identity.service -eq $name -and
        [int]$identity.port -eq [int]$PortByService[$name] -and
        $expectedScript.StartsWith(([IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase) -and
        $commandLine.IndexOf($expectedScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        [IO.Path]::GetFullPath($executablePath).Equals($expectedExecutable, [StringComparison]::OrdinalIgnoreCase)
    if (-not $identityMatches) {
        if ($listener -or $process) { $conflicts += @{ service = $name; port = $PortByService[$name]; pid = $processId; reason = 'identity-mismatch' } }
        continue
    }
    if (-not $DryRun -and $PSCmdlet.ShouldProcess("PID $processId ($name)", 'Terminate verified Mindspace service tree')) {
        $graceful = $false
        if ($name -eq 'asr' -and [string]$identity.nonce) {
            try {
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($PortByService[$name])/shutdown" -Headers @{ 'X-Mindspace-Service-Token' = [string]$identity.nonce } -TimeoutSec 3 | Out-Null
                $deadline = [DateTime]::UtcNow.AddSeconds(30)
                do {
                    Start-Sleep -Milliseconds 300
                    $stillListening = Get-NetTCPConnection -State Listen -LocalPort $PortByService[$name] -ErrorAction SilentlyContinue
                    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
                } while (($stillListening -or $process) -and [DateTime]::UtcNow -lt $deadline)
                $graceful = -not $stillListening -and -not $process
            } catch { $graceful = $false }
        }
        if (-not $graceful -and $name -eq 'api') {
            & taskkill.exe /PID $processId /T /F 2>$null | Out-Null
            Start-Sleep -Milliseconds 500
            $graceful = -not (Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue)
        }
        if (-not $graceful) {
            $conflicts += @{ service = $name; port = $PortByService[$name]; pid = $processId; reason = 'graceful-stop-timeout' }
            continue
        }
        Remove-Item -LiteralPath $identityPath -Force -ErrorAction SilentlyContinue
    }
    $stopped += @{ service = $name; port = $PortByService[$name]; pid = $processId; dry_run = [bool]$DryRun }
}

if ($IncludeQwen) {
    $distro = if ($env:MINDSPACE_QWEN3_WSL_DISTRO) { [string]$env:MINDSPACE_QWEN3_WSL_DISTRO } else { 'MindspaceVLLM' }
    $runtimeRoot = if ($env:MINDSPACE_QWEN3_RUNTIME_ROOT) { [string]$env:MINDSPACE_QWEN3_RUNTIME_ROOT } else { Join-Path $ProjectRoot 'environment\qwen3-vllm' }
    $qwenPidPath = Join-Path $runtimeRoot 'qwen3-vllm.pid'
    $qwenOwnerPath = Join-Path $runtimeRoot 'qwen3-vllm.owner'
    $qwenPid = if (Test-Path -LiteralPath $qwenPidPath) { [string](Get-Content -LiteralPath $qwenPidPath -Raw) } else { '' }
    $qwenOwner = if (Test-Path -LiteralPath $qwenOwnerPath) { [string](Get-Content -LiteralPath $qwenOwnerPath -Raw) } else { '' }
    if ($qwenPid.Trim() -match '^\d+$' -and $qwenOwner.Trim() -notmatch '^[a-f0-9-]{16,}$') {
        $conflicts += @{ service = 'qwenTts'; port = $PortByService.qwenTts; pid = [int]$qwenPid.Trim(); reason = 'qwen-owner-missing-or-invalid' }
    }
    if ($qwenPid.Trim() -match '^\d+$' -and $qwenOwner.Trim() -match '^[a-f0-9-]{16,}$' -and -not $DryRun -and $PSCmdlet.ShouldProcess("Qwen PID $($qwenPid.Trim())", 'Stop verified Mindspace Qwen process')) {
        & wsl.exe --distribution $distro -- bash -lc 'tr "\0" "\n" < "/proc/$1/environ" 2>/dev/null | grep -Fxq "MINDSPACE_QWEN_OWNER=$2" || exit 42; kill -TERM "$1" 2>/dev/null; for _ in $(seq 1 120); do kill -0 "$1" 2>/dev/null || exit 0; sleep 0.25; done; exit 43' mindspace-qwen $qwenPid.Trim() $qwenOwner.Trim() 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Remove-Item -LiteralPath $qwenPidPath -Force -ErrorAction SilentlyContinue }
        else { $conflicts += @{ service = 'qwenTts'; port = $PortByService.qwenTts; pid = [int]$qwenPid.Trim(); reason = 'qwen-identity-mismatch' } }
    }
    if ($qwenPid.Trim() -notmatch '^\d+$' -or $conflicts.service -notcontains 'qwenTts') {
        $stopped += @{ service = 'qwenTts'; distro = $distro; dry_run = [bool]$DryRun }
    }
}

$result = @{ ok = $conflicts.Count -eq 0; stopped = $stopped; conflicts = $conflicts }
$result | ConvertTo-Json -Depth 6 -Compress
if ($conflicts.Count) { exit 23 }
