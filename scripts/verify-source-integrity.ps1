[CmdletBinding()]
param(
    [ValidateSet('Baseline', 'Verify')]
    [string]$Mode = 'Verify',
    [string]$SourceRoot = 'A:\Mindscape',
    [string]$LauncherAsar = 'A:\Mindscape-app\Mindspace Launcher\resources\app.asar',
    [string]$ManifestPath = (Join-Path $PSScriptRoot '..\runtime\source-integrity.json')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
    if ($Mode -eq 'Baseline') { throw "Cannot create integrity baseline because source root does not exist: $SourceRoot" }
    Write-Warning "Legacy read-only source is not present; integrity verification skipped: $SourceRoot"
    Write-Output "SOURCE_INTEGRITY=skipped_missing_legacy_source"
    exit 0
}
$sourceRootResolved = (Resolve-Path -LiteralPath $SourceRoot).Path
$files = Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'backend') -File -Recurse |
    Where-Object {
        $_.Extension -in @('.py', '.js', '.css', '.html') -and
        $_.FullName -notmatch '\\.venv' -and
        $_.FullName -notmatch '\\runtime\\'
    }
$extra = @(
    (Join-Path $SourceRoot 'README.md'),
    (Join-Path $SourceRoot 'requirements.txt'),
    (Join-Path $SourceRoot 'start.bat'),
    $LauncherAsar
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
$files = @($files) + @(Get-Item -LiteralPath $extra)

$snapshot = foreach ($file in $files | Sort-Object FullName -Unique) {
    $relative = if ($file.FullName.StartsWith($sourceRootResolved)) {
        $file.FullName.Substring($sourceRootResolved.Length).TrimStart('\')
    } else {
        $file.FullName
    }
    [ordered]@{
        path = $relative
        full_path = $file.FullName
        bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    }
}

if ($Mode -eq 'Baseline') {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ManifestPath) | Out-Null
    [ordered]@{
        schema_version = '1.0.0'
        created_at = [DateTimeOffset]::Now.ToString('o')
        source_root = $sourceRootResolved
        files = @($snapshot)
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
    Write-Host "Integrity baseline created: $ManifestPath ($($snapshot.Count) files)"
    exit 0
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Integrity baseline not found: $ManifestPath"
}
$baseline = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$currentByPath = @{}
foreach ($item in $snapshot) { $currentByPath[$item.full_path] = $item }
$changes = foreach ($item in $baseline.files) {
    $current = $currentByPath[$item.full_path]
    if (-not $current) {
        [ordered]@{ path = $item.path; status = 'missing' }
    } elseif ($current.sha256 -ne $item.sha256) {
        [ordered]@{ path = $item.path; status = 'changed'; before = $item.sha256; after = $current.sha256 }
    }
}
if ($changes) {
    $changes | Format-Table -AutoSize
    throw "Source integrity verification failed: $($changes.Count) file(s) changed"
}
Write-Host "Source integrity verified: $($baseline.files.Count) files unchanged"
