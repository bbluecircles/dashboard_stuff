param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$DryRun,
    [switch]$SkipStagingIndexes
)

# Claims warehouse clock: {st}.period / pat_dt grew a month.
# Upsert Type 1 NPIs, then slide the 12-month window if warehouse max minus
# 2 months is past the mart window_end. Never phase1.
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "pd-sync.ps1") `
    -Change WindowSlide `
    -State $State `
    -Root $Root `
    -DryRun:$DryRun `
    -SkipStagingIndexes:$SkipStagingIndexes
exit $LASTEXITCODE
