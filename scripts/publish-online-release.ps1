[CmdletBinding(DefaultParameterSetName = 'Local')]
param(
    [Parameter(Mandatory)] [ValidateSet('stable', 'beta')] [string]$Channel,
    [Parameter(Mandatory, ParameterSetName = 'Local')] [string]$WebRoot,
    [Parameter(Mandatory, ParameterSetName = 'Ssh')] [string]$Remote,
    [Parameter(Mandatory, ParameterSetName = 'Ssh')] [string]$RemoteRoot,
    [switch]$AllowUnsignedLauncher
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SiteRoot = Join-Path $ProjectRoot 'runtime\release-site\mindspace'
$Catalog = Join-Path $SiteRoot "catalog\$Channel\windows-x64.json"
if (-not (Test-Path -LiteralPath $Catalog)) { throw "尚未生成 $Channel 发布目录" }
$catalogData = Get-Content -LiteralPath $Catalog -Raw | ConvertFrom-Json
$includeLauncher = $null -ne $catalogData.launcher
if ($includeLauncher) {
    $launchers = @(Get-ChildItem -LiteralPath (Join-Path $SiteRoot "launcher\$Channel") -Filter '*.exe' -File -ErrorAction SilentlyContinue)
    if (-not $launchers.Count) { throw 'Catalog 声明了 Launcher 更新，但发布目录没有安装包' }
    foreach ($launcher in $launchers) {
        $signature = Get-AuthenticodeSignature -LiteralPath $launcher.FullName
        if ($signature.Status -ne 'Valid' -and -not $AllowUnsignedLauncher) {
            throw "Launcher 没有有效 Authenticode 签名，拒绝上传：$($launcher.Name) · $($signature.Status)"
        }
    }
}

if ($PSCmdlet.ParameterSetName -eq 'Local') {
    $destination = [IO.Path]::GetFullPath($WebRoot)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $folders = @('core')
    if ($includeLauncher) { $folders += 'launcher' }
    foreach ($folder in $folders) {
        $source = Join-Path $SiteRoot $folder
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force }
    }
    $legacySource = Join-Path $SiteRoot $Channel
    if (Test-Path -LiteralPath $legacySource) { Copy-Item -LiteralPath $legacySource -Destination $destination -Recurse -Force }
    $catalogTarget = Join-Path $destination "catalog\$Channel"
    New-Item -ItemType Directory -Path $catalogTarget -Force | Out-Null
    $temporary = Join-Path $catalogTarget 'windows-x64.json.next'
    Copy-Item -LiteralPath $Catalog -Destination $temporary -Force
    Move-Item -LiteralPath $temporary -Destination (Join-Path $catalogTarget 'windows-x64.json') -Force
    @{ ok = $true; mode = 'local'; root = $destination } | ConvertTo-Json -Compress
    exit 0
}

foreach ($tool in @('ssh.exe', 'scp.exe')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool 不可用，请先初始化 Mindspace 私有 MinGit 或安装 OpenSSH Client" }
}
$releaseId = [guid]::NewGuid().ToString('N')
$selectedRoot = Join-Path $ProjectRoot "runtime\release-upload-$releaseId"
New-Item -ItemType Directory -Path $selectedRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $SiteRoot 'core') -Destination $selectedRoot -Recurse -Force
Copy-Item -LiteralPath (Join-Path $SiteRoot $Channel) -Destination $selectedRoot -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $selectedRoot "catalog\$Channel") -Force | Out-Null
Copy-Item -LiteralPath $Catalog -Destination (Join-Path $selectedRoot "catalog\$Channel\windows-x64.json") -Force
if ($includeLauncher) { Copy-Item -LiteralPath (Join-Path $SiteRoot 'launcher') -Destination $selectedRoot -Recurse -Force }
$staging = "/tmp/mindspace-release-$releaseId"
ssh $Remote "mkdir -p '$staging'"
if ($LASTEXITCODE -ne 0) { throw '无法创建远程暂存目录' }
scp -r "$selectedRoot\*" "${Remote}:$staging/"
if ($LASTEXITCODE -ne 0) { throw '上传发布文件失败' }
$remoteCommand = @"
set -eu
mkdir -p '$RemoteRoot/core' '$RemoteRoot/launcher' '$RemoteRoot/$Channel' '$RemoteRoot/catalog/$Channel'
if [ -d '$staging/core' ]; then cp -a '$staging/core/.' '$RemoteRoot/core/'; fi
if [ -d '$staging/launcher' ]; then cp -a '$staging/launcher/.' '$RemoteRoot/launcher/'; fi
if [ -d '$staging/$Channel' ]; then cp -a '$staging/$Channel/.' '$RemoteRoot/$Channel/'; fi
cp '$staging/catalog/$Channel/windows-x64.json' '$RemoteRoot/catalog/$Channel/windows-x64.json.next'
mv -f '$RemoteRoot/catalog/$Channel/windows-x64.json.next' '$RemoteRoot/catalog/$Channel/windows-x64.json'
rm -rf '$staging'
"@
ssh $Remote $remoteCommand
if ($LASTEXITCODE -ne 0) { throw '远程原子发布失败' }
Remove-Item -LiteralPath $selectedRoot -Recurse -Force
@{ ok = $true; mode = 'ssh'; remote = $Remote; root = $RemoteRoot } | ConvertTo-Json -Compress
