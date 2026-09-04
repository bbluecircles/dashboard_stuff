param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$DryRun
)

# Open Payments clock (CMS ~June and January). Reuses data/cms; will download the
# huge general file only if it is missing. Do not run on a timer next to a claims slide.
$ErrorActionPreference = "Stop"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python venv not found at $python"
}
Set-Location $Root
$syncArgs = @("-m", "provider_directory.cli", "sync", "--state", $State, "--open-payments")
if ($DryRun) { $syncArgs += "--dry-run" }
& $python @syncArgs
exit $LASTEXITCODE
