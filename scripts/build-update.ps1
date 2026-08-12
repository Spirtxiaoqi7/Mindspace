[CmdletBinding()]
param(
    [string]$Version,
    [string]$Channel = 'stable',
    [string]$BaseUrl = 'http://127.0.0.1:9780',
    [string]$OutputDirectory,
    [string]$Notes = '',
    [switch]$SkipBuild,
    [switch]$KeepStaging,
    [switch]$DryRun,
    [string]$StagingDirectory
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$AllowlistPath = Join-Path $ProjectRoot 'config\core-release-allowlist.json'
if (-not (Test-Path -LiteralPath $AllowlistPath -PathType Leaf)) { throw "Core release allowlist is missing: $AllowlistPath" }
$Allowlist = Get-Content -LiteralPath $AllowlistPath -Raw | ConvertFrom-Json
if ($Allowlist.schema_version -ne '1.0.0') { throw 'Unsupported Core release allowlist schema' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $ProjectRoot 'runtime\update-feed' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$PrivateKey = Join-Path $ProjectRoot 'runtime\update-keys\private.pem'
$PublicKey = Join-Path $ProjectRoot 'desktop\assets\update-public-key.pem'

if (-not $Version) {
    $match = Select-String -LiteralPath (Join-Path $ProjectRoot 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw 'Unable to read project version' }
    $Version = $match.Matches[0].Groups[1].Value
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') { throw 'Invalid semantic version' }
if (-not $SkipBuild) {
    node (Join-Path $PSScriptRoot 'sync-version.mjs') | Out-Null
    npm --prefix (Join-Path $ProjectRoot 'frontend') run build
}
if (-not $DryRun) {
    if (-not (Test-Path -LiteralPath $PrivateKey)) { node (Join-Path $PSScriptRoot 'generate-update-key.mjs') $PrivateKey $PublicKey | Out-Null }
    if (-not (Test-Path -LiteralPath $PublicKey)) { throw 'Update public key is missing' }
}

$Staging = if ($StagingDirectory) { [IO.Path]::GetFullPath($StagingDirectory) } else { Join-Path $ProjectRoot "runtime\update-build\$Version-$([guid]::NewGuid().ToString('N').Substring(0, 8))" }
if (Test-Path -LiteralPath $Staging) { throw "Staging directory already exists: $Staging" }
$Payload = Join-Path $Staging 'payload'
New-Item -ItemType Directory -Path $Payload -Force | Out-Null

function Copy-ReleaseFile([string]$Relative) {
    $source = Join-Path $ProjectRoot $Relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required release file is missing: $Relative" }
    $destination = Join-Path $Payload $Relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

foreach ($tree in $Allowlist.source_trees) {
    $relativeRoot = [string]$tree.path
    $sourceRoot = Join-Path $ProjectRoot $relativeRoot
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) { throw "Required release tree is missing: $relativeRoot" }
    $extensions = @($tree.extensions | ForEach-Object { ([string]$_).ToLowerInvariant() })
    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -Recurse -File -Force) {
        if ($file.FullName -match '\\(?:__pycache__|tests?|reports?|runtime|temp|tmp)\\') { continue }
        if ($extensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
        $relative = $file.FullName.Substring($ProjectRoot.Length).TrimStart('\')
        Copy-ReleaseFile $relative
    }
}
foreach ($relative in $Allowlist.runtime_files) { Copy-ReleaseFile ([string]$relative) }

$StagedPyproject = Join-Path $Payload 'pyproject.toml'
$PyprojectText = Get-Content -LiteralPath $StagedPyproject -Raw
$PyprojectText = [regex]::Replace($PyprojectText, '(?m)^version\s*=\s*"[^"]+"', "version = `"$Version`"", 1)
Set-Content -LiteralPath $StagedPyproject -Value $PyprojectText -Encoding utf8

# Hatchling reads project metadata while uv installs the bundled Core as an
# editable project. Validate every metadata file declared by pyproject before
# an archive is created, so a broken package cannot trigger a futile runtime
# rebuild on an end-user machine.
$MetadataFiles = @()
if ($PyprojectText -match '(?m)^readme\s*=\s*["'']([^"'']+)["'']') { $MetadataFiles += $Matches[1] }
if ($PyprojectText -match '(?m)^license\s*=\s*\{\s*file\s*=\s*["'']([^"'']+)["'']\s*\}') { $MetadataFiles += $Matches[1] }
if ($PyprojectText -match '(?m)^license-file\s*=\s*["'']([^"'']+)["'']') { $MetadataFiles += $Matches[1] }
foreach ($relative in $MetadataFiles | Select-Object -Unique) {
    if ([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)') { throw "Invalid Core metadata path: $relative" }
    if (-not (Test-Path -LiteralPath (Join-Path $Payload $relative) -PathType Leaf)) {
        throw "Core package metadata file is missing: $relative"
    }
}
Set-Content -LiteralPath (Join-Path $Payload 'src\mindspace_graph\version.py') -Encoding utf8 -Value @"
`"`"`"Build version synchronized from the project release source.`"`"`"

APP_VERSION = `"$Version`"
"@

$Targets = @($Allowlist.targets | ForEach-Object { [string]$_ })
[ordered]@{ schema_version = '2.0.0'; version = $Version; requires_dependency_sync = $false; allowlist = 'config/core-release-allowlist.json'; targets = $Targets } |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Payload 'payload.json') -Encoding utf8

$forbidden = @()
foreach ($file in Get-ChildItem -LiteralPath $Payload -Recurse -File -Force) {
    $relative = $file.FullName.Substring($Payload.Length).TrimStart('\')
    if ($file.Extension -eq '.map') { $forbidden += "$relative (source map)"; continue }
    if ($relative -match '(?i)(?:^|\\)(?:reports?|temp|tmp)(?:\\|$)') { $forbidden += "$relative (internal artifact)"; continue }
    if ($file.Extension -in @('.js', '.json', '.css', '.html') -and (Select-String -LiteralPath $file.FullName -SimpleMatch '"sourcesContent"' -Quiet)) {
        $forbidden += "$relative (embedded sourcesContent)"
    }
}
if ($forbidden.Count) { throw "Release payload contains forbidden files:`n$($forbidden -join "`n")" }

if ($DryRun) {
    $files = @(Get-ChildItem -LiteralPath $Payload -Recurse -File -Force)
    @{ ok = $true; dry_run = $true; staging = $Staging; payload = $Payload; files = $files.Count; targets = $Targets.Count } | ConvertTo-Json -Compress
    if (-not $KeepStaging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
    exit 0
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$PackageName = "mindspace-core-$Version.zip"
$PackagePath = Join-Path $OutputDirectory $PackageName
if (Test-Path -LiteralPath $PackagePath) { Remove-Item -LiteralPath $PackagePath -Force }
Compress-Archive -Path $Payload -DestinationPath $PackagePath -CompressionLevel Optimal
if (-not $Notes) { $Notes = "Mindspace $Version 核心更新" }
$ManifestPath = Join-Path $OutputDirectory 'manifest.json'
node (Join-Path $PSScriptRoot 'release-manifest.mjs') `
    "--version=$Version" "--channel=$Channel" "--base-url=$BaseUrl" `
    "--package=$PackagePath" "--private-key=$PrivateKey" "--output=$ManifestPath" `
    "--notes=$Notes" | Out-Null

if (-not $KeepStaging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
$PackageSize = (Get-Item -LiteralPath $PackagePath).Length
@{ manifest = $ManifestPath; package = $PackagePath; bytes = $PackageSize; version = $Version; staging = $(if ($KeepStaging) { $Staging } else { '' }) } | ConvertTo-Json -Compress
