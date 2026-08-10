$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$errors = [System.Collections.Generic.List[string]]::new()
$files = Get-ChildItem -LiteralPath $PSScriptRoot -File -Filter *.ps1 | Sort-Object FullName
foreach ($file in $files) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
    foreach ($parseError in $parseErrors) {
        $errors.Add("$($file.FullName.Substring($root.Length + 1)):$($parseError.Extent.StartLineNumber): $($parseError.Message)")
    }
}
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ }
    exit 1
}
Write-Output "PowerShell AST verified: $($files.Count) files"
