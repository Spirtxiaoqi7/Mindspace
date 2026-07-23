[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $ProjectRoot

$UvExe = if ($env:MINDSPACE_UV) { $env:MINDSPACE_UV } else { (Get-Command uv -ErrorAction Stop).Source }
$PythonExe = if ($env:MINDSPACE_CORE_PYTHON) { $env:MINDSPACE_CORE_PYTHON } else { Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $UvExe)) { throw 'Mindspace private uv is missing.' }
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Mindspace private Python environment is missing.' }
$env:UV_PROJECT_ENVIRONMENT = Split-Path -Parent (Split-Path -Parent $PythonExe)
& $UvExe sync --frozen --extra embeddings --project $ProjectRoot --python $PythonExe --no-managed-python
& $UvExe pip install --python $PythonExe pip --default-index 'https://mirrors.aliyun.com/pypi/simple/' --system-certs
& $PythonExe -c 'import fastapi, langgraph, sentence_transformers; print("MINDSPACE_REPAIR=ready")'

Write-Output 'Mindspace private runtime dependencies were repaired without development builds.'
