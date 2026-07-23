[CmdletBinding()]
param(
    [ValidateSet('stable', 'beta')] [string]$Channel = 'stable',
    [switch]$CompatibilityOnly,
    [switch]$StagingOnly,
    [string]$HostName = '123.57.245.201',
    [string]$UserName = 'root',
    [string]$Domain = 'douyinqijun.cn',
    [string]$SiteRoot = '/usr/share/nginx/html',
    [string]$HostFingerprint = 'SHA256:HWGsvrg+SfxU0ZOlJ85vDaP7KmqdQZ/GnNk50ZPE1JY'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ReleaseRoot = Join-Path $ProjectRoot 'runtime\release-site\mindspace'
$Log = Join-Path $ProjectRoot 'runtime\publish-online-release.log'

if (-not (Test-Path -LiteralPath $Python)) { throw "发布 Python 环境不存在：$Python" }
if (-not (Test-Path -LiteralPath (Join-Path $ReleaseRoot "catalog\$Channel\windows-x64.json"))) {
    throw "尚未生成 $Channel 发布目录"
}

$Secret = Read-Host '请输入官网服务器 SSH 密码（输入不会显示）' -AsSecureString
$Credential = [System.Net.NetworkCredential]::new('', $Secret)
$env:MINDSPACE_DEPLOY_PASSWORD = $Credential.Password
try {
    $DeployMode = if ($StagingOnly) { '--stage-release' } elseif ($CompatibilityOnly) { '--deploy-compatibility' } else { '--deploy-release' }
    & $Python (Join-Path $PSScriptRoot 'deploy-installer.py') `
        --host $HostName `
        --user $UserName `
        --fingerprint $HostFingerprint `
        --domain $Domain `
        --site-root $SiteRoot `
        $DeployMode `
        --release-root $ReleaseRoot `
        --channel $Channel 2>&1 | Tee-Object -LiteralPath $Log
    if ($LASTEXITCODE -ne 0) { throw "官网发布失败，退出码：$LASTEXITCODE" }
    Write-Host "`n发布完成，日志：$Log" -ForegroundColor Green
} finally {
    Remove-Item Env:MINDSPACE_DEPLOY_PASSWORD -ErrorAction SilentlyContinue
    $Credential.Password = ''
    $Secret.Dispose()
}

Read-Host '按回车关闭此窗口'
