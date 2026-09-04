param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$DryRun
)

# Care Compare yearly clock: MIPS scores + utilization categories. Skips Open Payments.
$ErrorActionPreference = "Stop"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python venv not found at $python"
}
Set-Location $Root
$syncArgs = @("-m", "provider_directory.cli", "sync", "--state", $State, "--mips", "--utilization")
if ($DryRun) { $syncArgs += "--dry-run" }
& $python @syncArgs
exit $LASTEXITCODE
