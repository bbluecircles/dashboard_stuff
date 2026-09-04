param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$DryRun
)

# Warehouse clock: if az (or other state) has a new usable month, slide the 12-month window
# and refresh E/M + POS. Does not phase1. Does not reread Open Payments.
$ErrorActionPreference = "Stop"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python venv not found at $python"
}
Set-Location $Root
$syncArgs = @("-m", "provider_directory.cli", "sync", "--state", $State)
if ($DryRun) { $syncArgs += "--dry-run" }
& $python @syncArgs
exit $LASTEXITCODE
