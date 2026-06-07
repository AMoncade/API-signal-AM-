# Launch the read API and open the visual demo dashboard at /demo.
# Runs from anywhere (resolves its own folder + venv). Ctrl+C to stop the server.
#
#   .\dashboard.ps1            serve + open http://127.0.0.1:8000/demo
#   .\dashboard.ps1 8080       serve on a different port
# The Swagger API explorer is also available at /docs.
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
$demo = "http://127.0.0.1:$port/demo"
Write-Host "Visual dashboard: $demo" -ForegroundColor Cyan
Write-Host "API explorer:     http://127.0.0.1:$port/docs   (Ctrl+C to stop)" -ForegroundColor Cyan
# open the visual dashboard shortly after the server comes up
Start-Job -ScriptBlock { param($u) Start-Sleep 2; Start-Process $u } -ArgumentList $demo | Out-Null
& $py -m uvicorn api.main:app --reload --port $port
