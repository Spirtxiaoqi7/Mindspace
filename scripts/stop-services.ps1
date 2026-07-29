[CmdletBinding()]
param(
    [switch]$IncludeQwen
)

$ErrorActionPreference = 'Stop'
$AllowedPorts = @(8765, 8766, 5055)
if ($IncludeQwen) { $AllowedPorts += 8091 }
$Listeners = Get-NetTCPConnection -State Listen -LocalPort $AllowedPorts -ErrorAction SilentlyContinue
$ProcessIds = @($Listeners | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($ProcessId in $ProcessIds) {
    if ($ProcessId -gt 0 -and $ProcessId -ne $PID) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    }
}
@{ ok = $true; stopped = $ProcessIds } | ConvertTo-Json -Compress
