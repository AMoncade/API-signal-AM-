# Launch the read API and open the interactive Swagger dashboard at /docs.
# Runs from anywhere (resolves its own folder + venv). Ctrl+C to stop the server.
#
#   .\dashboard.ps1            serve on http://127.0.0.1:8000/docs
#   .\dashboard.ps1 8080       serve on a different port
#
# If PowerShell blocks the script, run:  powershell -ExecutionPolicy Bypass -File .\dashboard.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv not found at $py" -ForegroundColor Red
    Write-Host "From the repo folder run:  uv sync --extra dev"
    exit 1
}
Set-Location $root
$port = if ($args.Count -ge 1) { $args[0] } else { 8000 }
$url = "http://127.0.0.1:$port/docs"
Write-Host "Dashboard: $url   (Ctrl+C to stop)" -ForegroundColor Cyan
# open the browser shortly after the server comes up
Start-Job -ScriptBlock { param($u) Start-Sleep 2; Start-Process $u } -ArgumentList $url | Out-Null
& $py -m uvicorn api.main:app --reload --port $port
