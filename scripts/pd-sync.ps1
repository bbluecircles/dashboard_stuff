<#
.SYNOPSIS
    Run the provider-directory mart refresh that matches a dependency-dataset change.

.DESCRIPTION
    The mart does not poll CMS or Trilliant. Someone (you or Task Scheduler) runs a
    clock when a source actually changed.

    "Warehouse max" / "Trilliant max" means:
        SELECT MAX(period_code) FROM {st}.period
    That is how far Trilliant has loaded claim months onto this MariaDB. It is not a
    CMS program year. A 2-month lag is applied because the newest warehouse months
    are incomplete. Example: max 202409 -> usable profile end 202407.

    CMS 2025 (Open Payments / MIPS / utilization files) is a different clock from
    claims period_code 202501. You can have OP 2025 dollars on a profile whose
    visits are still Aug 2023-Jul 2024.

    Never phase1 on a live mart.

.PARAMETER Change
    Which dependency changed. Use -Change List to print the catalog.

.PARAMETER State
    USPS code. Selects {st} / {st}al / {st}_pd. Default AZ.

.PARAMETER Root
    Analysis Scripts checkout (venv + .env + data/cms). Not the git clone.

.PARAMETER DryRun
    sync clocks print a JSON plan and write nothing. phase3-5 clocks print the
    command and exit without running.

.PARAMETER Download
    For CMS file clocks: fetch into data/cms first. Omit if the cache already has
    the file (especially Open Payments ~9 GB).

.PARAMETER ReloadPdc
    With CmsDac: TRUNCATE cms_pdc_clinician and reload the DAC CSV from cache.

.PARAMETER SkipStagingIndexes
    Passed through to a WindowSlide if a slide actually runs.

.EXAMPLE
    .\pd-sync.ps1 -Change List

.EXAMPLE
    .\pd-sync.ps1 -Change Plan -State AZ -DryRun

.EXAMPLE
    .\pd-sync.ps1 -Change WindowSlide -State AZ
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "List",
        "Plan",
        "WindowSlide",
        "PhysicianRoster",
        "PayorMix",
        "Referrals",
        "RvuSchedule",
        "PracticeSites",
        "CmsIdentity",
        "CmsDac",
        "CmsFacilityDownload",
        "NppesDownload",
        "OpenPayments",
        "Mips",
        "Utilization",
        "CareCompare"
    )]
    [string]$Change,

    [string]$State = "AZ",

    [string]$Root = "C:\Users\jluna\Documents\Analysis Scripts",

    [switch]$DryRun,

    [switch]$Download,

    [switch]$ReloadPdc,

    [switch]$SkipStagingIndexes
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-PdCatalog {
    @(
        [pscustomobject]@{
            Change   = "Plan"
            Dataset  = "{st}.period (read-only check)"
            When     = "Before any claims work. Shows warehouse max vs mart window."
            Runs     = "sync --dry-run"
        }
        [pscustomobject]@{
            Change   = "WindowSlide"
            Dataset  = "{st}.period / {st}.pat_dt - new period_code month"
            When     = "Trilliant loaded a later claim month. Slide if MAX(period_code) minus 2 months is past mart window_end."
            Runs     = "sync (upsert Type 1s, then phase6 --slide + E/M/POS if usable)"
        }
        [pscustomobject]@{
            Change   = "PhysicianRoster"
            Dataset  = "{st}.physician - new or renamed Type 1 NPIs"
            When     = "Roster grew or names/specialty changed. Does not slide the window."
            Runs     = "sync --spine"
        }
        [pscustomobject]@{
            Change   = "PayorMix"
            Dataset  = "{st}.dash_physician_payor_all restated inside the current window"
            When     = "Payer dash rebuilt for months already in the mart. A slide will not see this."
            Runs     = "phase4"
        }
        [pscustomobject]@{
            Change   = "Referrals"
            Dataset  = "{st}.dash_physician_referrals_to_rendering restated"
            When     = "Referral dash rebuilt for the current window."
            Runs     = "phase5"
        }
        [pscustomobject]@{
            Change   = "RvuSchedule"
            Dataset  = "{st}al.procd - WORK_RVU / total RVU values changed"
            When     = "Fee schedule / RVU lookup updated. Rebuilds RVU columns for the frozen window."
            Runs     = "phase4"
        }
        [pscustomobject]@{
            Change   = "PracticeSites"
            Dataset  = "{st}.sl - site / POS attributes changed"
            When     = "Service-location table restated. Rebuilds top 5 sites and POS mix. No pat_dt rescan."
            Runs     = "phase3 then extras (E/M+POS only)"
        }
        [pscustomobject]@{
            Change   = "CmsIdentity"
            Dataset  = "cms_* already loaded (DAC / NPPES staging in {st}_pd)"
            When     = "Re-copy gender/school/age/in_system from staging onto pd_provider. No download."
            Runs     = "sync --cms"
        }
        [pscustomobject]@{
            Change   = "CmsDac"
            Dataset  = "CMS DAC National Downloadable File (mj5m-pzi6)"
            When     = "New clinician CSV in data/cms (or pass -Download). Reloads cms_pdc_clinician, then overlay."
            Runs     = "extras --reload-pdc (skip OP/MIPS/util) then overlay-cms"
        }
        [pscustomobject]@{
            Change   = "CmsFacilityDownload"
            Dataset  = "CMS Facility Affiliation (27ea-46a8)"
            When     = "Puts a new affiliation CSV in data/cms. Loading it into cms_pdc_facility_affil is not a sync clock (that load lives in phase1, which is banned on a live mart). Overlay uses the table already in {st}_pd."
            Runs     = "download-cms --skip-nppes then overlay-cms"
        }
        [pscustomobject]@{
            Change   = "NppesDownload"
            Dataset  = "CMS NPPES monthly V2 zip (~1.1 GB)"
            When     = "Downloads the zip into data/cms. Loading a new zip into cms_nppes_type1 is not a sync clock (phase1). Overlay uses rows already loaded."
            Runs     = "download-cms --skip-pdc then overlay-cms"
        }
        [pscustomobject]@{
            Change   = "OpenPayments"
            Dataset  = "openpaymentsdata.cms.gov general / research / ownership CSVs"
            When     = "CMS Sunshine Act publish (~June / January). Program year is CMS's, not {st}.period. Do not pair with WindowSlide."
            Runs     = "sync --open-payments"
        }
        [pscustomobject]@{
            Change   = "Mips"
            Dataset  = "Care Compare MIPS (a174-a962)"
            When     = "Yearly CMS quality file. Not claims RVU."
            Runs     = "sync --mips"
        }
        [pscustomobject]@{
            Change   = "Utilization"
            Dataset  = "Care Compare utilization (n0yb-util)"
            When     = "Yearly CMS Medicare utilization categories. Not OTP visit counts."
            Runs     = "sync --utilization"
        }
        [pscustomobject]@{
            Change   = "CareCompare"
            Dataset  = "MIPS + utilization together"
            When     = "Both Care Compare files landed."
            Runs     = "sync --mips --utilization"
        }
    )
}

function Get-PdPython {
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw "Python venv not found at $python. Copy provider_directory/ into Analysis Scripts and pip install -r requirements.txt."
    }
    return $python
}

function Invoke-PdCli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$CliArgs,
        [switch]$SupportsDryRun,
        [switch]$AlwaysRun
    )
    $argList = [System.Collections.Generic.List[string]]::new()
    foreach ($part in $CliArgs) { $argList.Add($part) }
    if ($DryRun -and $SupportsDryRun) {
        $argList.Add("--dry-run")
    }
    $display = "python -m provider_directory.cli $($argList -join ' ')"
    if ($DryRun -and -not $SupportsDryRun -and -not $AlwaysRun) {
        Write-Host "DRY RUN (this clock has no --dry-run): would run $display"
        return 0
    }
    Write-Host "-> $display"
    $python = Get-PdPython
    Push-Location $Root
    try {
        & $python -m provider_directory.cli @argList
        if ($null -eq $LASTEXITCODE) { return 0 }
        return $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

if ($Change -eq "List") {
    Get-PdCatalog | Format-Table -Wrap -AutoSize Change, Dataset, When, Runs
    Write-Host @"

Warehouse max (Trilliant) = SELECT MAX(period_code) FROM ${State}.period
Usable profile end        = that max minus 2 months, still 12 months long
CMS program year          = Open Payments / MIPS / utilization files, independent clock

Examples:
  .\pd-sync.ps1 -Change Plan -State AZ -DryRun
  .\pd-sync.ps1 -Change WindowSlide -State AZ
  .\pd-sync.ps1 -Change PhysicianRoster -State AZ
  .\pd-sync.ps1 -Change OpenPayments -State AZ
"@
    exit 0
}

$code = $State.ToLowerInvariant()
Write-Host "Market $State  claims=${code}  lookup=${code}al  mart=${code}_pd  dryRun=$DryRun"

$exit = 0
switch ($Change) {
    "Plan" {
        # Always prints the window plan. Never slides.
        $exit = Invoke-PdCli -CliArgs @("sync", "--state", $State, "--dry-run") -AlwaysRun
    }
    "WindowSlide" {
        $slideArgs = [System.Collections.Generic.List[string]]@(
            "sync", "--state", $State
        )
        if ($SkipStagingIndexes) { $slideArgs.Add("--skip-staging-indexes") }
        $exit = Invoke-PdCli -CliArgs $slideArgs.ToArray() -SupportsDryRun
    }
    "PhysicianRoster" {
        $exit = Invoke-PdCli -CliArgs @("sync", "--state", $State, "--spine") -SupportsDryRun
    }
    "PayorMix" {
        $exit = Invoke-PdCli -CliArgs @("phase4", "--state", $State)
    }
    "Referrals" {
        $exit = Invoke-PdCli -CliArgs @("phase5", "--state", $State)
    }
    "RvuSchedule" {
        $exit = Invoke-PdCli -CliArgs @("phase4", "--state", $State)
    }
    "PracticeSites" {
        $exit = Invoke-PdCli -CliArgs @("phase3", "--state", $State)
        if ($exit -eq 0) {
            $exit = Invoke-PdCli -CliArgs @(
                "extras", "--state", $State,
                "--skip-mips", "--skip-utilization", "--skip-open-payments"
            )
        }
    }
    "CmsIdentity" {
        $cmsArgs = [System.Collections.Generic.List[string]]@("sync", "--state", $State, "--cms")
        if ($ReloadPdc) { $cmsArgs.Add("--reload-pdc") }
        $exit = Invoke-PdCli -CliArgs $cmsArgs.ToArray() -SupportsDryRun
    }
    "CmsDac" {
        $dacArgs = [System.Collections.Generic.List[string]]@(
            "extras", "--state", $State,
            "--reload-pdc",
            "--skip-mips", "--skip-utilization", "--skip-open-payments"
        )
        if ($Download) { $dacArgs.Add("--download") }
        $exit = Invoke-PdCli -CliArgs $dacArgs.ToArray()
        if ($exit -eq 0) {
            $exit = Invoke-PdCli -CliArgs @("overlay-cms", "--state", $State)
        }
    }
    "CmsFacilityDownload" {
        Write-Warning "This updates data/cms and overlays in_system from cms_pdc_facility_affil already in the mart. A brand-new affiliation CSV is not loaded into MariaDB here (that path is phase1)."
        $exit = Invoke-PdCli -CliArgs @("download-cms", "--state", $State, "--skip-nppes")
        if ($exit -eq 0) {
            $exit = Invoke-PdCli -CliArgs @("overlay-cms", "--state", $State)
        }
    }
    "NppesDownload" {
        Write-Warning "This downloads the ~1.1 GB zip. Loading a new zip into cms_nppes_type1 is not wired as a sync clock (phase1). Overlay uses NPIs already staged."
        $exit = Invoke-PdCli -CliArgs @("download-cms", "--state", $State, "--skip-pdc")
        if ($exit -eq 0) {
            $exit = Invoke-PdCli -CliArgs @("overlay-cms", "--state", $State)
        }
    }
    "OpenPayments" {
        $exit = Invoke-PdCli -CliArgs @("sync", "--state", $State, "--open-payments") -SupportsDryRun
    }
    "Mips" {
        $exit = Invoke-PdCli -CliArgs @("sync", "--state", $State, "--mips") -SupportsDryRun
    }
    "Utilization" {
        $exit = Invoke-PdCli -CliArgs @("sync", "--state", $State, "--utilization") -SupportsDryRun
    }
    "CareCompare" {
        $exit = Invoke-PdCli -CliArgs @("sync", "--state", $State, "--mips", "--utilization") -SupportsDryRun
    }
}

if ($null -eq $exit) { $exit = 0 }
exit $exit
