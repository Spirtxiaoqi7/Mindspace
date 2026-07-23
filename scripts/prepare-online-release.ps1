[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Version,
    [Parameter(Mandatory)] [int]$Sequence,
    [ValidateSet('stable', 'beta')] [string]$Channel = 'stable',
    [ValidateRange(0, 100)] [int]$Rollout = 100,
    [string]$BaseUrl = 'https://douyinqijun.cn/downloads/mindspace',
    [string]$LauncherBaseUrl = 'https://douyinqijun.cn/downloads/mindspace',
    [string]$Notes = '',
    [string]$Title = '',
    [string]$MinimumLauncher = '0.4.0',
    [string]$OutputRoot = '',
    [switch]$IncludeLauncher,
    [switch]$AllowUnsignedLauncher,
    [switch]$MandatoryCore,
    [switch]$MandatoryLauncher,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SiteRoot = if ($OutputRoot) { [IO.Path]::GetFullPath($OutputRoot) } else { Join-Path $ProjectRoot 'runtime\release-site\mindspace' }
$CoreRelease = Join-Path $SiteRoot "core\releases\$Version"
$CatalogPath = Join-Path $SiteRoot "catalog\$Channel\windows-x64.json"
$LegacyPath = Join-Path $SiteRoot "$Channel\manifest.json"
$PrivateKey = Join-Path $ProjectRoot 'runtime\update-keys\private.pem'
$LauncherFeedUrl = "$($LauncherBaseUrl.TrimEnd('/'))/launcher/$Channel/"

if (-not (Test-Path -LiteralPath $PrivateKey)) { throw '发布私钥不存在，拒绝生成未签名版本' }
New-Item -ItemType Directory -Path $CoreRelease, (Split-Path -Parent $CatalogPath), (Split-Path -Parent $LegacyPath) -Force | Out-Null

$buildArguments = @{
    Version = $Version
    Channel = $Channel
    BaseUrl = "$($BaseUrl.TrimEnd('/'))/core/releases/$Version"
    OutputDirectory = $CoreRelease
    Notes = $Notes
}
if ($SkipBuild) { $buildArguments.SkipBuild = $true }
& (Join-Path $PSScriptRoot 'build-update.ps1') @buildArguments
$CoreManifest = Join-Path $CoreRelease 'manifest.json'
Copy-Item -LiteralPath $CoreManifest -Destination $LegacyPath -Force

$catalogArguments = @(
    (Join-Path $PSScriptRoot 'release-catalog.mjs'),
    "--core-manifest=$CoreManifest",
    "--output=$CatalogPath",
    "--private-key=$PrivateKey",
    "--channel=$Channel",
    "--sequence=$Sequence",
    "--rollout=$Rollout",
    "--minimum-launcher=$MinimumLauncher",
    "--mandatory-core=$($MandatoryCore.IsPresent.ToString().ToLowerInvariant())"
    "--history-file=$(Join-Path $ProjectRoot 'docs\release-history.json')"
)
if ($Notes) { $catalogArguments += "--notes=$Notes" }
if ($Title) { $catalogArguments += "--title=$Title" }

if ($IncludeLauncher) {
    if (-not $SkipBuild) {
        # The packaged Launcher embeds the freshly-built Core as its offline
        # bootstrap fallback. Keep that feed in sync before electron-builder
        # runs, otherwise a clean release can accidentally embed the previous
        # Core version or fail because the expected archive is absent.
        $BootstrapFeed = Join-Path $ProjectRoot 'runtime\update-feed'
        New-Item -ItemType Directory -Path $BootstrapFeed -Force | Out-Null
        Copy-Item -LiteralPath $CoreManifest -Destination (Join-Path $BootstrapFeed 'manifest.json') -Force
        Copy-Item `
            -LiteralPath (Join-Path $CoreRelease "mindspace-core-$Version.zip") `
            -Destination (Join-Path $BootstrapFeed "mindspace-core-$Version.zip") `
            -Force
        npm --prefix (Join-Path $ProjectRoot 'desktop') run package:app
    }
    $LauncherRoot = Join-Path $SiteRoot "launcher\$Channel"
    New-Item -ItemType Directory -Path $LauncherRoot -Force | Out-Null
    $Installer = Join-Path $ProjectRoot "dist-launcher\Mindspace-$Version-x64.exe"
    $Blockmap = "$Installer.blockmap"
    $Latest = Join-Path $ProjectRoot 'dist-launcher\latest.yml'
    if (-not (Test-Path -LiteralPath $Installer)) { throw "Launcher 发布文件缺失：$Installer" }
    $signature = Get-AuthenticodeSignature -LiteralPath $Installer
    if ($signature.Status -ne 'Valid' -and -not $AllowUnsignedLauncher) {
        throw "Launcher 安装包没有有效 Authenticode 签名，拒绝正式发布。测试时可显式使用 -AllowUnsignedLauncher。当前状态：$($signature.Status)"
    }
    foreach ($file in @($Installer, $Blockmap, $Latest)) {
        if (-not (Test-Path -LiteralPath $file)) { throw "Launcher 发布文件缺失：$file" }
        Copy-Item -LiteralPath $file -Destination $LauncherRoot -Force
    }
    $catalogArguments += "--launcher-version=$Version"
    $catalogArguments += "--launcher-feed=$LauncherFeedUrl"
    $catalogArguments += "--mandatory-launcher=$($MandatoryLauncher.IsPresent.ToString().ToLowerInvariant())"
}

node @catalogArguments
@{
    ok = $true
    version = $Version
    sequence = $Sequence
    channel = $Channel
    rollout = $Rollout
    launcher_included = $IncludeLauncher.IsPresent
    upload_root = $SiteRoot
    catalog = $CatalogPath
} | ConvertTo-Json -Depth 5
