param([string]$DataDir = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$frontendPath = Join-Path $projectRoot 'frontend'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install backend/requirements.lock.txt first. See README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'node_modules\vite\bin\vite.js'))) { throw 'Run npm ci in frontend first. See README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'vite.config.ts'))) { throw 'Missing frontend/vite.config.ts. Restore the Vite configuration before starting Tirek.' }
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop the existing server or open http://127.0.0.1:5173 if Tirek is already running."
    }
}
if ($DataDir) { $env:DATA_DIR = [System.IO.Path]::GetFullPath($DataDir) }
$logPath = Join-Path $projectRoot '.logs'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$apiProcess = $null
$webProcess = $null
try {
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'api.out.log') -RedirectStandardError (Join-Path $logPath 'api.err.log')
    $webProcess = Start-Process -FilePath (Get-Command node).Source -ArgumentList @('node_modules/vite/bin/vite.js', '--config', 'vite.config.ts', '--host', '127.0.0.1', '--port', '5173') -WorkingDirectory $frontendPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'web.out.log') -RedirectStandardError (Join-Path $logPath 'web.err.log')
    $ready = $false
    $startupDeadline = (Get-Date).AddSeconds(30)
    do {
        $apiProcess.Refresh()
        $webProcess.Refresh()
        if ($apiProcess.HasExited -or $webProcess.HasExited) { throw 'A server exited during startup. Check .logs/api.err.log and .logs/web.err.log.' }
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:5173/api/v1/health' -TimeoutSec 2
            $workspace = Invoke-RestMethod 'http://127.0.0.1:5173/api/v1/workspace' -TimeoutSec 2
            $ready = $health.status -eq 'ok' -and $null -ne $workspace.capabilities -and $null -ne $workspace.datasets
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
        if ($null -ne $taskProcess -and -not $taskProcess.HasExited) { Stop-Process -Id $taskProcess.Id -ErrorAction SilentlyContinue }
    }
}
