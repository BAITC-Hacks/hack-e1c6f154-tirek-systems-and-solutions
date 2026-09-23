param([string]$DataDir = '', [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot '.venv-ml\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonPath)) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
}
$pythonPath = [System.IO.Path]::GetFullPath($PythonPath)
$frontendPath = Join-Path $projectRoot 'frontend'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install backend/requirements.lock.txt first. See README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'node_modules\vite\bin\vite.js'))) { throw 'Run npm ci in frontend first. See README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'vite.config.ts'))) { throw 'Missing frontend/vite.config.ts. Restore the Vite configuration before starting Tirek.' }
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodePath = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $projectRoot '.tools\node-v22.14.0-win-x64\node.exe' }
if (-not (Test-Path -LiteralPath $nodePath)) { throw 'Install Node.js 22.12+ or run scripts/setup.ps1.' }
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop the existing server or open http://127.0.0.1:5173 if Tirek is already running."
    }
}
$previousDataDir = $env:DATA_DIR
if ($DataDir) { $env:DATA_DIR = [System.IO.Path]::GetFullPath($DataDir) }
$logPath = Join-Path $projectRoot '.logs'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$apiProcess = $null
$webProcess = $null
try {
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'api.out.log') -RedirectStandardError (Join-Path $logPath 'api.err.log')
    $webProcess = Start-Process -FilePath $nodePath -ArgumentList @('node_modules/vite/bin/vite.js', '--config', 'vite.config.ts', '--host', '127.0.0.1', '--port', '5173') -WorkingDirectory $frontendPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'web.out.log') -RedirectStandardError (Join-Path $logPath 'web.err.log')
    $ready = $false
    $startupDeadline = (Get-Date).AddSeconds(30)
    do {
        $apiProcess.Refresh()
        $webProcess.Refresh()
        if ($apiProcess.HasExited -or $webProcess.HasExited) { throw 'A server exited during startup. Check .logs/api.err.log and .logs/web.err.log.' }
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:5173/api/v1/health' -TimeoutSec 2
            $ready = $health.status -eq 'ok'
        } catch {
            $ready = $false
        }
        if (-not $ready) { Start-Sleep -Milliseconds 500 }
    } while (-not $ready -and (Get-Date) -lt $startupDeadline)
    if (-not $ready) { throw 'Tirek did not become ready: API requests through Vite failed. Check .logs/ and frontend/vite.config.ts.' }
    Write-Host 'Tirek: http://127.0.0.1:5173'
    Write-Host 'API:   http://127.0.0.1:8000/docs'
    Write-Host 'Logs:  .logs/ | Ctrl+C stops both servers.'
    while (-not $apiProcess.HasExited -and -not $webProcess.HasExited) {
        Start-Sleep -Seconds 1
        $apiProcess.Refresh()
        $webProcess.Refresh()
    }
    throw 'A server exited. Check .logs/api.err.log and .logs/web.err.log.'
} finally {
    foreach ($taskProcess in @($apiProcess, $webProcess)) {
        if ($null -ne $taskProcess) {
            $taskProcess.Refresh()
            if (-not $taskProcess.HasExited) {
                # Windows venv launchers spawn a second python.exe; stop the owned
                # process tree, not only the launcher, so port 8000 is released.
                & taskkill.exe /PID $taskProcess.Id /T /F 2>$null | Out-Null
            }
        }
    }
    if ($DataDir) { $env:DATA_DIR = $previousDataDir }
}
