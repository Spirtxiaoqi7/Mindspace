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
        & taskkill.exe /PID $processId /T /F 2>$null | Out-Null
        Remove-Item -LiteralPath $identityPath -Force -ErrorAction SilentlyContinue
    }
    $stopped += @{ service = $name; port = $PortByService[$name]; pid = $processId; dry_run = [bool]$DryRun }
}

if ($IncludeQwen) {
    $distro = if ($env:MINDSPACE_QWEN3_WSL_DISTRO) { [string]$env:MINDSPACE_QWEN3_WSL_DISTRO } else { 'MindspaceVLLM' }
    if (-not $DryRun -and $PSCmdlet.ShouldProcess("WSL distro $distro", 'Terminate dedicated Mindspace Qwen runtime')) {
        & wsl.exe --terminate $distro 2>$null | Out-Null
        $runtimeRoot = if ($env:MINDSPACE_QWEN3_RUNTIME_ROOT) { [string]$env:MINDSPACE_QWEN3_RUNTIME_ROOT } else { Join-Path $ProjectRoot 'environment\qwen3-vllm' }
        Remove-Item -LiteralPath (Join-Path $runtimeRoot 'qwen3-vllm.pid') -Force -ErrorAction SilentlyContinue
    }
    $stopped += @{ service = 'qwenTts-distro'; distro = $distro; dry_run = [bool]$DryRun }
}

$result = @{ ok = $conflicts.Count -eq 0; stopped = $stopped; conflicts = $conflicts }
$result | ConvertTo-Json -Depth 6 -Compress
if ($conflicts.Count) { exit 23 }
