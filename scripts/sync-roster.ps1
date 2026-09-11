param(
    [string]$State = "AZ",
    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",
    [switch]$DryRun
)

# Trilliant {st}.physician grew. Insert missing Type 1s, refresh name/specialty. Never truncates. Does not slide.
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "pd-sync.ps1") `
    -Change PhysicianRoster `
    -State $State `
    -Root $Root `
    -DryRun:$DryRun
exit $LASTEXITCODE
