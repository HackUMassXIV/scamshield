# Windows wrapper kept for convenience; the real logic is in run.py (same on every OS).
#   powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $py run.py demo @args
