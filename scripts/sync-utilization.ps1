param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$DryRun
)

# Care Compare utilization categories only (n0yb-util).
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "pd-sync.ps1") `
    -Change Utilization `
    -State $State `
    -Root $Root `
    -DryRun:$DryRun
exit $LASTEXITCODE
