[CmdletBinding()]
param(
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

$currentRoot = [System.IO.Path]::GetFullPath("A:\RAG\langgarph-rag").TrimEnd("\")
$currentRelease = Join-Path $currentRoot "dist-mindspace-app"
$currentUserData = [System.IO.Path]::GetFullPath("C:\Users\Administrator\AppData\Roaming\mindspace-desktop").TrimEnd("\")

$targets = @(
    "A:\Mindscape",
    "A:\Mindscape-app",
    "A:\Mindspace-release-resources",
    "C:\Users\Administrator\AppData\Local\Mindspace",
    "C:\Users\Administrator\AppData\Local\Mindspace-app",
    "C:\Users\Administrator\AppData\Roaming\mindspace-launcher",
    "C:\Users\Administrator\Desktop\Mindspace Launcher.lnk",
    "C:\Users\Administrator\Desktop\Mindspace.lnk",
    (Join-Path $currentRoot "dist-launcher"),
    (Join-Path $currentRoot "dist-launcher-prototype"),
    (Join-Path $currentRoot "dist-application"),
    (Join-Path $currentRoot "dist-application-final"),
    (Join-Path $currentRoot "desktop\.capture-profile"),
    (Join-Path $currentRoot "desktop\.capture-profile-final2"),
    (Join-Path $currentRoot "desktop\.capture-profile-release"),
    (Join-Path $currentRoot "desktop\.builder-cache\app-stage-final"),
    (Join-Path $currentRoot "desktop\.builder-cache\app-stage-final2"),
    (Join-Path $currentRoot "desktop\application-packaged-final.png"),
    (Join-Path $currentRoot "desktop\application-packaged-final2.png"),
    (Join-Path $currentRoot "desktop\mindspace-release.png"),
    (Join-Path $currentRoot "desktop\prototype-launcher-packaged.png"),
    (Join-Path $currentRoot "desktop\prototype-launcher.png"),
    (Join-Path $currentRoot "dist\mindspace-graph-portable"),
    (Join-Path $currentRoot "dist\smoke-runtime"),
    (Join-Path $currentRoot "dist\smoke-venv"),
    (Join-Path $currentRoot "dist\mindspace-graph-portable.zip"),
    (Join-Path $currentRoot "dist\mindspace_langgraph-0.2.0-py3-none-any.whl")
)

$records = foreach ($target in $targets) {
    $fullPath = [System.IO.Path]::GetFullPath($target).TrimEnd("\")

    if ($fullPath -match "(?i)ARPM") {
        throw "安全保护：目标包含 ARPM：$fullPath"
    }
    if ($fullPath -eq $currentRoot -or $fullPath -eq $currentRelease -or $fullPath -eq $currentUserData) {
        throw "安全保护：目标属于当前版本保留区：$fullPath"
    }

    $exists = Test-Path -LiteralPath $fullPath
    $bytes = 0L
    if ($exists) {
        $item = Get-Item -LiteralPath $fullPath -Force
        if ($item.PSIsContainer) {
            $bytes = [long]((Get-ChildItem -LiteralPath $fullPath -Recurse -File -Force -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum)
        } else {
            $bytes = [long]$item.Length
        }

        if ($Execute) {
            Remove-Item -LiteralPath $fullPath -Recurse -Force
        }
    }

    [pscustomobject]@{
        path = $fullPath
        existed = $exists
        bytes = $bytes
        removed = [bool]($Execute -and $exists -and -not (Test-Path -LiteralPath $fullPath))
    }
}

if ($Execute) {
    $shortcutPath = "C:\Users\Administrator\Desktop\Mindspace.lnk"
    $targetExe = Join-Path $currentRelease "win-unpacked\Mindspace.exe"
    if (-not (Test-Path -LiteralPath $targetExe)) {
        throw "当前版本可执行文件不存在：$targetExe"
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetExe
    $shortcut.WorkingDirectory = Split-Path $targetExe
    $shortcut.Description = "Mindspace 本地 AI 应用"
    $shortcut.IconLocation = "$targetExe,0"
    $shortcut.Save()
}

$summary = [pscustomobject]@{
    executed = [bool]$Execute
    currentRoot = $currentRoot
    currentRelease = $currentRelease
    currentUserData = $currentUserData
    reclaimedBytes = [long](($records | Where-Object removed | Measure-Object -Property bytes -Sum).Sum)
    records = @($records)
}

$logRoot = Join-Path $currentRoot "runtime\logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logRoot "legacy-mindspace-cleanup-$stamp.json"
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $logPath -Encoding utf8

$summary
Write-Output "LOG=$logPath"
