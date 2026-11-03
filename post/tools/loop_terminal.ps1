# Runs the live "which flight" script again and again, for filming. Stop with Ctrl+C.
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$py = Join-Path $root "data\vendor\doppler-locate\.venv\Scripts\python.exe"
while ($true) {
    & $py (Join-Path $PSScriptRoot "live_flight_id.py")
    Start-Sleep -Seconds 3
    Clear-Host
}
