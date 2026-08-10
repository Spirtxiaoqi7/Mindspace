[CmdletBinding()]
param(
    [ValidateSet('Baseline', 'Verify')]
    [string]$Mode = 'Verify',
    [string]$SourceRoot,
    [string]$ManifestPath
)

$ErrorActionPreference = 'Stop'
if (-not $SourceRoot) { $SourceRoot = if ($env:MINDSPACE_SOURCE_ROOT) { $env:MINDSPACE_SOURCE_ROOT } else { (Resolve-Path (Join-Path $PSScriptRoot '..')).Path } }
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
if (-not $ManifestPath) { $ManifestPath = Join-Path $SourceRoot 'runtime\source-integrity.json' }
if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) { throw "Authoritative source root does not exist: $SourceRoot" }
foreach ($required in @('pyproject.toml', 'src\mindspace_graph', 'desktop', 'scripts', 'frontend')) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot $required))) { throw "Authoritative source root is incomplete: $required" }
}

$excluded = '\\(?:\.git|node_modules|__pycache__|\.pytest_cache|\.mypy_cache|dist|dist-launcher|runtime|reports|coverage|\.venv[^\\]*)\\'
$extensions = @('.py', '.pyi', '.js', '.cjs', '.mjs', '.ts', '.tsx', '.css', '.html', '.json', '.ps1', '.toml', '.lock', '.yml', '.yaml', '.md')
$files = Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Force |
    Where-Object { $_.FullName -notmatch $excluded -and ($extensions -contains $_.Extension -or $_.Name -in @('uv.lock')) }
$snapshot = foreach ($file in $files | Sort-Object FullName -Unique) {
    [ordered]@{
        path = $file.FullName.Substring($SourceRoot.Length).TrimStart('\').Replace('\', '/')
        bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
if (-not $snapshot.Count) { throw 'Authoritative source snapshot is empty' }

if ($Mode -eq 'Baseline') {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ManifestPath) | Out-Null
    [ordered]@{ schema_version = '2.0.0'; created_at = [DateTimeOffset]::Now.ToString('o'); source_root = $SourceRoot; files = @($snapshot) } |
        ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
    Write-Output "SOURCE_INTEGRITY=baseline files=$($snapshot.Count)"
    exit 0
}
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Integrity baseline not found: $ManifestPath" }
$baseline = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$current = @{}; foreach ($item in $snapshot) { $current[$item.path] = $item }
$expected = @{}; foreach ($item in $baseline.files) { $expected[[string]$item.path] = $item }
$changes = @()
foreach ($path in ($current.Keys + $expected.Keys | Select-Object -Unique)) {
    if (-not $current[$path]) { $changes += @{ path = $path; status = 'missing' } }
    elseif (-not $expected[$path]) { $changes += @{ path = $path; status = 'added' } }
    elseif ($current[$path].sha256 -ne $expected[$path].sha256) { $changes += @{ path = $path; status = 'changed' } }
}
if ($changes.Count) { $changes | ConvertTo-Json -Depth 4; throw "Source integrity verification failed: $($changes.Count) file(s) differ" }
Write-Output "SOURCE_INTEGRITY=verified files=$($snapshot.Count)"
