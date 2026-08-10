Set-StrictMode -Version Latest

function Get-MindspaceServicePorts {
    [CmdletBinding()]
    param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path)

    $registryPath = Join-Path $ProjectRoot 'config\service-ports.json'
    if (-not (Test-Path -LiteralPath $registryPath -PathType Leaf)) {
        throw "Mindspace service port registry is missing: $registryPath"
    }
    $registry = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
    if ($registry.schema_version -ne '1.0.0' -or $registry.host -ne '127.0.0.1') {
        throw 'Mindspace service port registry schema or host is invalid'
    }
    $result = [ordered]@{}
    foreach ($entry in @(
        @('core', 'MINDSPACE_PORT'),
        @('asr', 'MINDSPACE_ASR_PORT'),
        @('tts', 'MINDSPACE_TTS_PORT'),
        @('qwen', 'MINDSPACE_QWEN3_PORT')
    )) {
        $name, $environmentName = $entry
        $environmentValue = [Environment]::GetEnvironmentVariable($environmentName)
        $value = if ($environmentValue) { $environmentValue } else { $registry.services.$name }
        $port = 0
        if (-not [int]::TryParse([string]$value, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
            throw "Invalid $name port: $value"
        }
        $result[$name] = $port
    }
    if (@($result.Values | Select-Object -Unique).Count -ne $result.Count) {
        throw 'Mindspace service ports must be unique'
    }
    return [pscustomobject]$result
}
