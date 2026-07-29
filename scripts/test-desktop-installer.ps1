[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Installer,
    [Parameter(Mandatory)]
    [string]$InstallRoot,
    [Parameter(Mandatory)]
    [string]$HomeRoot,
    [string]$ExpectedVersion = '0.5.52',
    [string]$ExecutableName = 'MindspaceInstallerQA.exe',
    [string]$ReportDirectory
)

$ErrorActionPreference = 'Stop'
$Installer = [IO.Path]::GetFullPath($Installer)
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$HomeRoot = [IO.Path]::GetFullPath($HomeRoot)
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ReportDirectory) {
    $ReportDirectory = Join-Path $ProjectRoot 'runtime\installer-qa'
}
$ReportDirectory = [IO.Path]::GetFullPath($ReportDirectory)

function Assert-QaPath([string]$Path, [string]$Label) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if ($resolved -notmatch '(?i)[\\/]MindspaceInstallerQA(?:[\\/]|$)') {
        throw "$Label 必须位于明确的 MindspaceInstallerQA 隔离目录内：$resolved"
    }
    if ($resolved -in @(
        [IO.Path]::GetPathRoot($resolved),
        'A:\Mindspace',
        'A:\Mindspace\application'
    )) {
        throw "$Label 指向受保护的正式目录：$resolved"
    }
}

function Invoke-Installer([string[]]$Arguments) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $Installer -ArgumentList $Arguments -PassThru -Wait
    $timer.Stop()
    if ($process.ExitCode -ne 0) {
        throw "安装器退出码为 $($process.ExitCode)"
    }
    return [math]::Round($timer.Elapsed.TotalSeconds, 3)
}

function Stop-QaProcesses([string]$ImagePath) {
    $target = [IO.Path]::GetFullPath($ImagePath)
    Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($target)) -ErrorAction SilentlyContinue |
        Where-Object {
            try { [IO.Path]::GetFullPath($_.Path) -eq $target } catch { $false }
        } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

Assert-QaPath $InstallRoot 'InstallRoot'
Assert-QaPath $HomeRoot 'HomeRoot'
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "安装器不存在：$Installer"
}
if (Test-Path -LiteralPath $InstallRoot) {
    throw "隔离安装目录必须是新的空路径：$InstallRoot"
}

$appPath = Join-Path $InstallRoot $ExecutableName
$capturePath = Join-Path $HomeRoot 'launcher-first-start.png'
$sentinelPath = Join-Path $HomeRoot 'data\installer-qa-preserve.json'
$previousHome = $env:MINDSPACE_HOME
$previousSkipMigration = $env:MINDSPACE_SKIP_LEGACY_MIGRATION
$result = [ordered]@{
    schema_version = '1.0.0'
    expected_version = $ExpectedVersion
    installer = $Installer
    installer_sha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    installer_bytes = (Get-Item -LiteralPath $Installer).Length
    authenticode = (Get-AuthenticodeSignature -LiteralPath $Installer).Status.ToString()
    install_root = $InstallRoot
    home_root = $HomeRoot
    started_at = (Get-Date).ToUniversalTime().ToString('o')
}

try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $HomeRoot) -Force | Out-Null
    $result.fresh_install_seconds = Invoke-Installer @('/S', '/currentuser', "/D=$InstallRoot")
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
        throw "全新安装后缺少主程序：$appPath"
    }
    $fileVersion = (Get-Item -LiteralPath $appPath).VersionInfo.ProductVersion
    $result.installed_product_version = $fileVersion
    if ($fileVersion -notlike "$ExpectedVersion*") {
        throw "安装版本不一致：期望 $ExpectedVersion，实际 $fileVersion"
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $sentinelPath) -Force | Out-Null
    '{"preserve":true}' | Set-Content -LiteralPath $sentinelPath -Encoding utf8
    $env:MINDSPACE_HOME = $HomeRoot
    $env:MINDSPACE_SKIP_LEGACY_MIGRATION = '1'
    $env:MINDSPACE_CAPTURE_DELAY_MS = '3500'
    $capture = Start-Process -FilePath $appPath -ArgumentList "--capture=$capturePath" -PassThru -Wait
    if ($capture.ExitCode -ne 0) {
        throw "隔离首次启动退出码为 $($capture.ExitCode)"
    }
    if (-not (Test-Path -LiteralPath $capturePath -PathType Leaf)) {
        throw '首次启动未生成 Launcher 验收截图'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $HomeRoot 'application\core\pyproject.toml'))) {
        throw '首次启动未展开内置 Core'
    }
    $result.first_launch_capture = $capturePath
    $result.bootstrap_core_ready = $true

    $running = Start-Process -FilePath $appPath -PassThru
    Start-Sleep -Seconds 3
    $result.running_upgrade_seconds = Invoke-Installer @('/S', '/currentuser', "/D=$InstallRoot")
    Start-Sleep -Milliseconds 600
    $remaining = @(
        Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($appPath)) -ErrorAction SilentlyContinue |
            Where-Object {
                try { [IO.Path]::GetFullPath($_.Path) -eq $appPath } catch { $false }
            }
    )
    if ($remaining.Count) {
        throw '覆盖安装后仍有旧版 QA 进程存活'
    }
    if ($result.running_upgrade_seconds -gt 20) {
        throw "运行中覆盖安装耗时超过 20 秒：$($result.running_upgrade_seconds)"
    }
    if (-not (Test-Path -LiteralPath $sentinelPath)) {
        throw '覆盖安装改写了隔离用户数据'
    }
    $result.running_upgrade_closed_old_process = $true
    $result.user_data_preserved_after_upgrade = $true

    $uninstaller = Get-ChildItem -LiteralPath $InstallRoot -Filter 'Uninstall*.exe' -File |
        Select-Object -First 1
    if (-not $uninstaller) {
        throw '安装目录缺少卸载器'
    }
    $uninstallTimer = [Diagnostics.Stopwatch]::StartNew()
    $uninstall = Start-Process -FilePath $uninstaller.FullName -ArgumentList @('/S', '/currentuser') -PassThru -Wait
    $uninstallTimer.Stop()
    if ($uninstall.ExitCode -ne 0) {
        throw "卸载器退出码为 $($uninstall.ExitCode)"
    }
    Start-Sleep -Milliseconds 800
    if (Test-Path -LiteralPath $appPath) {
        throw '静默卸载后主程序仍然存在'
    }
    if (-not (Test-Path -LiteralPath $sentinelPath)) {
        throw '静默卸载删除了隔离用户数据'
    }
    $result.uninstall_seconds = [math]::Round($uninstallTimer.Elapsed.TotalSeconds, 3)
    $result.application_removed = $true
    $result.user_data_preserved_after_uninstall = $true
    $result.status = 'passed'
}
catch {
    $result.status = 'failed'
    $result.error = $_.Exception.Message
    throw
}
finally {
    Stop-QaProcesses $appPath
    if ($null -eq $previousHome) { Remove-Item Env:MINDSPACE_HOME -ErrorAction SilentlyContinue }
    else { $env:MINDSPACE_HOME = $previousHome }
    if ($null -eq $previousSkipMigration) {
        Remove-Item Env:MINDSPACE_SKIP_LEGACY_MIGRATION -ErrorAction SilentlyContinue
    }
    else { $env:MINDSPACE_SKIP_LEGACY_MIGRATION = $previousSkipMigration }
    $result.completed_at = (Get-Date).ToUniversalTime().ToString('o')
    New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
    $reportPath = Join-Path $ReportDirectory "installer-qa-$ExpectedVersion-$((Get-Date).ToString('yyyyMMdd-HHmmss')).json"
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Output "INSTALLER_QA_REPORT=$reportPath"
}
