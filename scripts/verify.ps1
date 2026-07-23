[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Verification requires PowerShell 7 or newer.'
}

uv sync --extra dev --extra embeddings
uv run ruff format --check src tests scripts\download-asr-models.py
uv run ruff check src tests scripts\download-asr-models.py
uv run pytest -q

npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run build
npm --prefix desktop ci
npm --prefix desktop run check
npm --prefix desktop run build

& (Join-Path $PSScriptRoot 'verify-source-integrity.ps1')

uv build --wheel
$wheel = Get-ChildItem -LiteralPath '.\dist' -Filter '*.whl' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $wheel -or $wheel.Length -le 0) {
    throw 'Wheel verification failed.'
}

Write-Output "VERIFIED_WHEEL=$($wheel.FullName)"
