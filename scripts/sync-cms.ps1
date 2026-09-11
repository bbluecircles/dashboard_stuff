param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$ReloadPdc,
    [switch]$DryRun,
    [switch]$Download
)

# CMS identity. -ReloadPdc reloads the DAC clinician CSV (mj5m-pzi6).
$ErrorActionPreference = "Stop"
$change = if ($ReloadPdc) { "CmsDac" } else { "CmsIdentity" }
& (Join-Path $PSScriptRoot "pd-sync.ps1") `
    -Change $change `
    -State $State `
    -Root $Root `
    -DryRun:$DryRun `
    -Download:$Download `
    -ReloadPdc:$ReloadPdc
exit $LASTEXITCODE
