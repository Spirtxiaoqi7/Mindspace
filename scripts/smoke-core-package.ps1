[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Package,
    [int]$Port = 9876
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimeRoot = Join-Path $ProjectRoot 'runtime'
$Package = (Resolve-Path -LiteralPath $Package).Path
$SmokeRoot = Join-Path $RuntimeRoot "package-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$Payload = Join-Path $SmokeRoot 'payload'
$SmokeData = Join-Path $SmokeRoot 'user-data'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

New-Item -ItemType Directory -Path $SmokeRoot, $SmokeData -Force | Out-Null
Expand-Archive -LiteralPath $Package -DestinationPath $SmokeRoot
if (-not (Test-Path -LiteralPath (Join-Path $Payload 'src\mindspace_graph\server.py'))) {
    throw 'Packaged Core source is missing'
}

& $Python (Join-Path $PSScriptRoot 'smoke_core_package.py') $Payload $SmokeData
if ($LASTEXITCODE -ne 0) { throw 'Packaged Core smoke test failed' }
