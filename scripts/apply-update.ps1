[CmdletBinding(DefaultParameterSetName = 'Apply')]
param(
    [Parameter(Mandatory)] [string]$Root,
    [Parameter(Mandatory, ParameterSetName = 'Apply')] [string]$Package,
    [Parameter(Mandatory, ParameterSetName = 'Apply')] [string]$Version,
    [Parameter(Mandatory, ParameterSetName = 'Rollback')] [string]$RollbackToken
)

$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
$UpdateRoot = Join-Path $Root 'runtime\updates'
$AllowedTargets = @(
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

function Assert-UnderRoot([string]$Path, [string]$ExpectedRoot) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = [IO.Path]::GetFullPath($ExpectedRoot).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem operation outside $ExpectedRoot`: $resolved"
    }
    return $resolved
}

function Replace-Target([string]$Source, [string]$Target) {
    Assert-UnderRoot $Target $Root | Out-Null
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    $parent = Split-Path -Parent $Target
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    if (Test-Path -LiteralPath $Source -PathType Container) {
        Copy-Item -LiteralPath $Source -Destination $Target -Recurse -Force
    }
    else {
        $temporary = "$Target.update-partial"
        Assert-UnderRoot $temporary $Root | Out-Null
        Copy-Item -LiteralPath $Source -Destination $temporary -Force
        Move-Item -LiteralPath $temporary -Destination $Target -Force
    }
}

New-Item -ItemType Directory -Path $UpdateRoot -Force | Out-Null

if ($PSCmdlet.ParameterSetName -eq 'Rollback') {
    if ($RollbackToken -notmatch '^[a-zA-Z0-9._-]+$') {
        throw 'Invalid rollback token'
    }
    $BackupRoot = Assert-UnderRoot (Join-Path $UpdateRoot "backups\$RollbackToken") $UpdateRoot
    $MetadataPath = Join-Path $BackupRoot 'backup.json'
    if (-not (Test-Path -LiteralPath $MetadataPath)) {
        throw "Rollback metadata not found: $RollbackToken"
    }
    $Metadata = Get-Content -LiteralPath $MetadataPath -Raw | ConvertFrom-Json
    foreach ($item in $Metadata.targets) {
        $relative = [string]$item.path
        if ($AllowedTargets -notcontains $relative) { throw "Unsafe rollback target: $relative" }
        $target = Assert-UnderRoot (Join-Path $Root $relative) $Root
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        if ([bool]$item.existed) {
            $source = Assert-UnderRoot (Join-Path $BackupRoot "payload\$relative") $BackupRoot
            Replace-Target $source $target
        }
    }
    $Current = @{
        version = [string]$Metadata.previous_version
        rolled_back_from = [string]$Metadata.version
        rolled_back_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
    $Current | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $UpdateRoot 'current.json') -Encoding utf8
    @{ ok = $true; action = 'rollback'; version = $Current.version; rollback_token = $RollbackToken } |
        ConvertTo-Json -Compress
    exit 0
}

if (-not (Test-Path -LiteralPath (Join-Path $Root 'pyproject.toml'))) {
    throw "Mindspace root is invalid: $Root"
}
$Package = [IO.Path]::GetFullPath($Package)
if (-not (Test-Path -LiteralPath $Package -PathType Leaf)) { throw "Update package not found: $Package" }
if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') { throw 'Invalid semantic version' }

$Token = "$Version-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$StagingRoot = Assert-UnderRoot (Join-Path $UpdateRoot "staging\$Token") $UpdateRoot
$BackupRoot = Assert-UnderRoot (Join-Path $UpdateRoot "backups\$Token") $UpdateRoot
New-Item -ItemType Directory -Path $StagingRoot, (Join-Path $BackupRoot 'payload') -Force | Out-Null
Expand-Archive -LiteralPath $Package -DestinationPath $StagingRoot -Force
$PayloadRoot = Join-Path $StagingRoot 'payload'
$PayloadManifestPath = Join-Path $PayloadRoot 'payload.json'
if (-not (Test-Path -LiteralPath $PayloadManifestPath)) { throw 'Update payload.json is missing' }
$Payload = Get-Content -LiteralPath $PayloadManifestPath -Raw | ConvertFrom-Json
if ([string]$Payload.version -ne $Version) { throw 'Update payload version does not match manifest' }

$Targets = @($Payload.targets | ForEach-Object { [string]$_ })
if (-not $Targets.Count) { throw 'Update contains no targets' }
foreach ($relative in $Targets) {
    if ($AllowedTargets -notcontains $relative) { throw "Unsafe update target: $relative" }
    $source = Assert-UnderRoot (Join-Path $PayloadRoot $relative) $PayloadRoot
    if (-not (Test-Path -LiteralPath $source)) { throw "Payload target is missing: $relative" }
}

$CurrentPath = Join-Path $UpdateRoot 'current.json'
$PreviousVersion = ''
if (Test-Path -LiteralPath $CurrentPath) {
    try { $PreviousVersion = [string](Get-Content -LiteralPath $CurrentPath -Raw | ConvertFrom-Json).version } catch {}
}
if (-not $PreviousVersion) {
    $VersionMatch = Select-String -LiteralPath (Join-Path $Root 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($VersionMatch) { $PreviousVersion = $VersionMatch.Matches[0].Groups[1].Value }
}

$BackupTargets = @()
foreach ($relative in $Targets) {
    $target = Assert-UnderRoot (Join-Path $Root $relative) $Root
    $existed = Test-Path -LiteralPath $target
    $BackupTargets += @{ path = $relative; existed = $existed }
    if ($existed) {
        $backup = Assert-UnderRoot (Join-Path $BackupRoot "payload\$relative") $BackupRoot
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
        Copy-Item -LiteralPath $target -Destination $backup -Recurse -Force
    }
}
$BackupMetadata = @{
    version = $Version
    previous_version = $PreviousVersion
    created_at = [DateTimeOffset]::UtcNow.ToString('o')
    targets = $BackupTargets
}
$BackupMetadata | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $BackupRoot 'backup.json') -Encoding utf8

try {
    foreach ($relative in $Targets) {
        Replace-Target (Join-Path $PayloadRoot $relative) (Join-Path $Root $relative)
    }
    $Current = @{
        version = $Version
        previous_version = $PreviousVersion
        installed_at = [DateTimeOffset]::UtcNow.ToString('o')
        rollback_token = $Token
    }
    $Current | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $CurrentPath -Encoding utf8
}
catch {
    & $PSCommandPath -Root $Root -RollbackToken $Token | Out-Null
    throw
}
finally {
    if (Test-Path -LiteralPath $StagingRoot) { Remove-Item -LiteralPath $StagingRoot -Recurse -Force }
}

@{ ok = $true; action = 'apply'; version = $Version; previous_version = $PreviousVersion; rollback_token = $Token } |
    ConvertTo-Json -Compress
