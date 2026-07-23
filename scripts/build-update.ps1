[CmdletBinding()]
param(
    [string]$Version,
    [string]$Channel = 'stable',
    [string]$BaseUrl = 'http://127.0.0.1:9780',
    [string]$OutputDirectory,
    [string]$Notes = '',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
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
if (-not (Test-Path -LiteralPath $PrivateKey)) {
    node (Join-Path $PSScriptRoot 'generate-update-key.mjs') $PrivateKey $PublicKey | Out-Null
}
if (-not (Test-Path -LiteralPath $PublicKey)) { throw 'Update public key is missing' }

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$Staging = Join-Path $ProjectRoot "runtime\update-build\$Version-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$Payload = Join-Path $Staging 'payload'
New-Item -ItemType Directory -Path $Payload -Force | Out-Null

$Targets = @(
    'src\mindspace_graph',
    'scripts',
    'vendor\cosyvoice_mindspace_worker.py',
    'vendor\gpt_sovits_mindspace_worker.py',
    'vendor\GPT-SoVITS\GPT_SoVITS',
    'vendor\GPT-SoVITS\tools',
    'vendor\GPT-SoVITS\LICENSE',
    'vendor\CosyVoice\cosyvoice',
    'vendor\CosyVoice\third_party\Matcha-TTS\matcha',
    'vendor\CosyVoice\LICENSE',
    'vendor\CosyVoice\third_party\Matcha-TTS\LICENSE',
    'config\gpt-sovits-voices.json',
    'pyproject.toml',
    'uv.lock',
    'README.md'
)
foreach ($relative in $Targets) {
    $source = Join-Path $ProjectRoot $relative
    $destination = Join-Path $Payload $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
}

$ReleaseOnlyScripts = @(
    'build-update.ps1',
    'generate-update-key.mjs',
    'prepare-online-release.ps1',
    'publish-online-release.ps1',
    'release-catalog.mjs',
    'release-manifest.mjs',
    'sign-runtime-manifest.mjs',
    'sync-version.mjs',
    'test-update-e2e.ps1',
    'verify-online-release.mjs'
)
foreach ($name in $ReleaseOnlyScripts) {
    $releaseTool = Join-Path $Payload "scripts\$name"
    if (Test-Path -LiteralPath $releaseTool) { Remove-Item -LiteralPath $releaseTool -Force }
}

Get-ChildItem -LiteralPath $Payload -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $Payload -Recurse -File -Include '*.pyc','*.pyo' | Remove-Item -Force
$StagedPyproject = Join-Path $Payload 'pyproject.toml'
$PyprojectText = Get-Content -LiteralPath $StagedPyproject -Raw
$PyprojectText = [regex]::Replace($PyprojectText, '(?m)^version\s*=\s*"[^"]+"', "version = `"$Version`"", 1)
Set-Content -LiteralPath $StagedPyproject -Value $PyprojectText -Encoding utf8
Set-Content -LiteralPath (Join-Path $Payload 'src\mindspace_graph\version.py') -Encoding utf8 -Value @"
`"`"`"Build version synchronized from the project release source.`"`"`"

APP_VERSION = `"$Version`"
"@
@{
    schema_version = '1.0.0'
    version = $Version
    requires_dependency_sync = $false
    targets = $Targets
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Payload 'payload.json') -Encoding utf8

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

Remove-Item -LiteralPath $Staging -Recurse -Force
$PackageSize = (Get-Item -LiteralPath $PackagePath).Length
@{ manifest = $ManifestPath; package = $PackagePath; bytes = $PackageSize; version = $Version } | ConvertTo-Json -Compress
