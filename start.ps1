$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $bootstrapPython = $null
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notmatch 'WindowsApps') {
        $bootstrapPython = $pythonCommand.Source
    }
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (-not $bootstrapPython -and (Test-Path -LiteralPath $bundledPython)) {
        $bootstrapPython = $bundledPython
    }
    if (-not $bootstrapPython) { throw 'Please install Python 3.11+ and add it to PATH.' }
    & $bootstrapPython -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Python environment.' }
}
& $venvPython -c 'import fastapi, uvicorn, httpx'
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
}
& $venvPython -X utf8 (Join-Path $projectRoot 'run.py') @args
exit $LASTEXITCODE
