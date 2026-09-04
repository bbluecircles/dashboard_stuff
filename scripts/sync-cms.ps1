param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$ReloadPdc,
    [switch]$DryRun
)

# CMS identity clock: overlay PDC/NPPES already in data/cms onto {st}_pd.
# Pass -ReloadPdc only when a new DAC file must replace cms_pdc_clinician.
$ErrorActionPreference = "Stop"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python venv not found at $python"
}
Set-Location $Root
$syncArgs = @("-m", "provider_directory.cli", "sync", "--state", $State, "--cms")
if ($ReloadPdc) { $syncArgs += "--reload-pdc" }
if ($DryRun) { $syncArgs += "--dry-run" }
& $python @syncArgs
exit $LASTEXITCODE
