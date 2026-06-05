# Run a Signals-API scan from anywhere (resolves its own folder + venv).
#
#   .\scan.ps1                          full nightly pipeline (run-all; 8-K uses your
#                                       configured provider in .env)
#   .\scan.ps1 form_d --date 20260603   any worker job + args (free: form_d/seed/snapshot)
#   .\scan.ps1 run-all --date 20260603  one specific day, all signals
#
# If PowerShell blocks the script, run:  powershell -ExecutionPolicy Bypass -File .\scan.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv not found at $py" -ForegroundColor Red
    Write-Host "From the repo folder run:  uv sync --extra dev"
    exit 1
}
Set-Location $root
if ($args.Count -eq 0) {
    & $py -m worker run-all
} else {
    & $py -m worker @args
}
