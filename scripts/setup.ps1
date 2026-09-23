param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
if (-not $PythonPath) {
    $portable = Join-Path $projectRoot '.tools\python312\python.exe'
    if (Test-Path -LiteralPath $portable) { $PythonPath = $portable }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonPath = (& py -3.12 -c 'import sys; print(sys.executable)').Trim()
    } else { throw 'Install Python 3.12, then pass -PythonPath C:\path\to\python.exe.' }
}
& $PythonPath -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 required.' }
$venv = Join-Path $projectRoot '.venv-ml\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venv)) {
    & $PythonPath -m venv .venv-ml
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create virtual environment.' }
}
& $venv -m pip install -r backend/requirements-ml.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) {
    $nodeDir = Join-Path $projectRoot '.tools\node-v22.14.0-win-x64'
    if (-not (Test-Path -LiteralPath (Join-Path $nodeDir 'npm.cmd'))) { throw 'Install Node.js 22.12+.' }
    $env:PATH = $nodeDir + ';' + $env:PATH
    $npm = Get-Command npm.cmd
}
& $npm.Source --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
Write-Host 'Ready. Run .\scripts\dev.ps1 and open http://127.0.0.1:5173.'
