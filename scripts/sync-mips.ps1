param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$DryRun
)

# Care Compare yearly: MIPS + utilization. Not Open Payments. Not claims RVU.
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "pd-sync.ps1") `
    -Change CareCompare `
    -State $State `
    -Root $Root `
    -DryRun:$DryRun
exit $LASTEXITCODE
