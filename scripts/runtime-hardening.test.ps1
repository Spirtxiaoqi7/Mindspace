[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Pwsh = (Get-Process -Id $PID).Path
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) "mindspace-runtime-hardening-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
try {
    . (Join-Path $PSScriptRoot 'service-ports.ps1')
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $occupiedPort = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    $old = @{
        MINDSPACE_PORT = $env:MINDSPACE_PORT
        MINDSPACE_ASR_PORT = $env:MINDSPACE_ASR_PORT
        MINDSPACE_TTS_PORT = $env:MINDSPACE_TTS_PORT
        MINDSPACE_QWEN3_PORT = $env:MINDSPACE_QWEN3_PORT
    }
    $env:MINDSPACE_PORT = [string]$occupiedPort
    $env:MINDSPACE_ASR_PORT = [string]($occupiedPort + 1)
    $env:MINDSPACE_TTS_PORT = [string]($occupiedPort + 2)
    $env:MINDSPACE_QWEN3_PORT = [string]($occupiedPort + 3)
    $ports = Get-MindspaceServicePorts -ProjectRoot $ProjectRoot
    if ($ports.core -ne $occupiedPort) { throw 'Port environment override was not applied' }

    $identityRoot = Join-Path $TempRoot 'identities'
    New-Item -ItemType Directory -Path $identityRoot -Force | Out-Null
    $output = & $Pwsh -NoProfile -File (Join-Path $PSScriptRoot 'stop-services.ps1') -ProjectRoot $ProjectRoot -IdentityRoot $identityRoot -Services api 2>&1
    if ($LASTEXITCODE -ne 23) { throw "Unknown port owner was not reported as a conflict: $output" }
    if (-not $listener.Server.IsBound) { throw 'Unknown port owner was terminated' }

    $missing = Join-Path $TempRoot 'missing-source'
    & $Pwsh -NoProfile -File (Join-Path $PSScriptRoot 'verify-source-integrity.ps1') -SourceRoot $missing *> $null
    if ($LASTEXITCODE -eq 0) { throw 'Missing authoritative source incorrectly passed integrity verification' }

    $staging = Join-Path $TempRoot 'release-staging'
    $dryRunText = & $Pwsh -NoProfile -File (Join-Path $PSScriptRoot 'build-update.ps1') -Version '0.8.2' -SkipBuild -DryRun -KeepStaging -StagingDirectory $staging
    if ($LASTEXITCODE -ne 0) { throw "Release dry-run failed: $dryRunText" }
    $payload = Join-Path $staging 'payload'
    if (Get-ChildItem -LiteralPath $payload -Recurse -File -Filter '*.map') { throw 'Release dry-run included a source map' }
    if (Get-ChildItem -LiteralPath (Join-Path $payload 'scripts') -File | Where-Object Name -Match '(benchmark|acceptance|real_api|r18|gemma|deepseek|history|report)') {
        throw 'Release dry-run included an internal script'
    }
    $sourcesContent = Get-ChildItem -LiteralPath $payload -Recurse -File |
        Where-Object Extension -In @('.js', '.json', '.css', '.html') |
        Where-Object { Select-String -LiteralPath $_.FullName -SimpleMatch '"sourcesContent"' -Quiet } |
        Select-Object -First 1
    if ($sourcesContent) { throw "Release dry-run included sourcesContent: $($sourcesContent.FullName)" }

    $fakeRoot = Join-Path $TempRoot 'fake-core'
    New-Item -ItemType Directory -Path (Join-Path $fakeRoot 'config') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $fakeRoot 'pyproject.toml') -Value 'version = "0.8.1"' -Encoding utf8
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'config\core-release-allowlist.json') -Destination (Join-Path $fakeRoot 'config\core-release-allowlist.json')
    $maliciousZip = Join-Path $TempRoot 'malicious.zip'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::Open($maliciousZip, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $archive.CreateEntry('payload/../escape.txt')
        $writer = [IO.StreamWriter]::new($entry.Open())
        try { $writer.Write('escape') } finally { $writer.Dispose() }
    } finally { $archive.Dispose() }
    & $Pwsh -NoProfile -File (Join-Path $PSScriptRoot 'apply-update.ps1') -Root $fakeRoot -Package $maliciousZip -Version '0.8.2' *> $null
    if ($LASTEXITCODE -eq 0) { throw 'Path-traversal update archive was accepted' }
    if (Test-Path -LiteralPath (Join-Path $TempRoot 'escape.txt')) { throw 'Path-traversal archive escaped staging' }

    [ordered]@{ ok = $true; unknown_port_not_killed = $true; missing_source_failed = $true; release_allowlist = $true; no_maps = $true; traversal_rejected = $true } | ConvertTo-Json -Compress
}
finally {
    if ($listener) { $listener.Stop() }
    foreach ($name in $old.Keys) { [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process') }
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
