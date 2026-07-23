[CmdletBinding()]
param(
    [string]$SourceRoot = 'A:\Mindscape',
    [string]$TargetRoot = (Join-Path $PSScriptRoot '..\assets\models')
)

$ErrorActionPreference = 'Stop'
$models = @(
    @{
        Source = Join-Path $SourceRoot 'assets\models\shibing624\text2vec-base-chinese'
        Target = Join-Path $TargetRoot 'shibing624\text2vec-base-chinese'
        Required = @('config.json', 'vocab.txt', 'pytorch_model.bin', 'onnx\model.onnx')
    },
    @{
        Source = Join-Path $SourceRoot 'assets\models\tts\Fun-CosyVoice3-0.5B-2512'
        Target = Join-Path $TargetRoot 'tts\Fun-CosyVoice3-0.5B-2512'
        Required = @('cosyvoice3.yaml', 'llm.pt', 'flow.pt', 'hift.pt')
    }
)

New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
$manifest = @()
foreach ($model in $models) {
    if (-not (Test-Path -LiteralPath $model.Source -PathType Container)) {
        throw "Model source not found: $($model.Source)"
    }
    New-Item -ItemType Directory -Force -Path $model.Target | Out-Null
    Copy-Item -Path (Join-Path $model.Source '*') -Destination $model.Target -Recurse -Force

    $requiredFiles = foreach ($relative in $model.Required) {
        $sourceFile = Join-Path $model.Source $relative
        $targetFile = Join-Path $model.Target $relative
        if (-not (Test-Path -LiteralPath $targetFile -PathType Leaf)) {
            throw "Required model file missing after copy: $targetFile"
        }
        $sourceHash = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) {
            throw "Model checksum mismatch: $targetFile"
        }
        [ordered]@{
            relative_path = $relative
            bytes = (Get-Item -LiteralPath $targetFile).Length
            sha256 = $targetHash
        }
    }
    $allFiles = Get-ChildItem -LiteralPath $model.Target -File -Recurse
    $manifest += [ordered]@{
        source = $model.Source
        target = $model.Target
        files = $allFiles.Count
        bytes = ($allFiles | Measure-Object -Property Length -Sum).Sum
        required_files = @($requiredFiles)
    }
}

$manifestPath = Join-Path $TargetRoot 'models.manifest.json'
[ordered]@{
    schema_version = '1.0.0'
    generated_at = [DateTimeOffset]::Now.ToString('o')
    models = $manifest
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Host "Model synchronization complete: $manifestPath"
