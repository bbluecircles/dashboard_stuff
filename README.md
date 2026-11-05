# Arizona provider directory

Python mart first. FastAPI (`serve`) wraps the same functions (`get_provider`, `run_phase1`).

data dict => https://d3low7qefzg7gr.cloudfront.net/myfantasyai/assets/data-dict.csv  
snapshot => https://d3low7qefzg7gr.cloudfront.net/myfantasyai/assets/db_snapshot.json

## Phase 1

Identity spine from `az.physician` (Type 1 NPIs) plus CMS staging tables.

```
pip install -r requirements.txt
python -m pytest
python -m provider_directory.cli phase1
python -m provider_directory.cli phase1 --download --skip-nppes
python -m provider_directory.cli get 1234567893
```

`--download` pulls current CMS PDC CSVs (and the ~1.1 GB NPPES monthly zip unless `--skip-nppes`). Files land in `data/cms/` and are reused on the next run.

`in_system_provider` is filled from PDC facility affiliations (hospital CCN). After Phase 1 has already loaded `cms_pdc_facility_affil`, refresh the flag without rebuilding the spine:

```
python -m provider_directory.cli overlay-cms
python -m provider_directory.cli get 1952863797
```

Do **not** rerun `phase1` — that TRUNCATEs `pd_provider` and you would have to rerun 2–5.

Same DB env as `db_snapshot.py`: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`. `--state AZ` selects `az` / `azal` / `az_pd` (and `TX` → `tx` / `txal` / `tx_pd`). `--state AZ_CMS` is the blended Arizona warehouse: `az_cms` / `azal` / `az_pd` (USPS still `AZ` for PDC/NPPES). Optional env: `PD_MARKET_STATE` (default `AZ`; set `AZ_CMS` on the blended box). `PD_CLAIMS_DB` / `PD_LOOKUP_DB` / `PD_MART_DB` remain the import defaults when code does not pass `--state`.

**Reference server.** `master_ref.111_physician` and `master_ref.cms_group_practice` live on a different MariaDB host. Put its connection in the same `.env`: `REF_DB_HOST`, `REF_DB_PORT` (3306), `REF_DB_USER`, `REF_DB_PASSWORD`. When `REF_DB_HOST` is unset the pipeline assumes the tables are on the claims server and reuses `DB_*`. Optional: `PD_REF_DB` (`master_ref`), `PD_REF_PHYSICIAN_TABLE` (`111_physician`), `PD_REF_GROUP_PRACTICE_TABLE` (`cms_group_practice`), `PD_REF_COPY_PAGE_ROWS` (20000 rows per page) and `PD_REF_COPY_MAX_STATEMENT_SECONDS` (30). The pipeline never joins across servers: it copies those two tables into the mart with short keyset-paged reads on an autocommit, READ UNCOMMITTED session with a per-statement time limit, so it holds no locks on `111_physician`, and builds from the copy. Check the details with:

```
python -m provider_directory.cli ref-check
```

It prints host, user, engine, approximate rows, primary key, columns and one sample row for each table. Read-only.

Then copy and overlay:

```
python -m provider_directory.cli sync --state AZ_CMS --ref
python -m provider_directory.cli ref-compare --state AZ_CMS
```

Run `sync --ref` before Phase 4 (or `ref-overlay` after it) so the group key is filled; a full Phase 4 re-applies the overlay itself when `pd_src_physician` has rows. `sync --ref` copies every `111_physician` row for the market state (all NPI types, so organizations are named) plus any spine NPI whose reference address is out of state into `pd_src_physician`, and every `cms_group_practice` row whose group or clinician is in the state into `pd_src_group_practice`, each built as a `_new` table and swapped in with one `RENAME`. It then writes the group key `primary_organization_id` / `primary_organization_name` (the master file's group practice id, the CMS group PAC ID), the specialty (`primary_specialty_code` / `_description` / `specialty_classification` from the master file's `npi_spec_grp` columns when the row has one; the Phase 1 warehouse value stays otherwise), `affiliation_code` / `affiliation_name` / `affiliation_source` and `reference_group_id` / `reference_group_name` onto `pd_provider`, with the `'888'+NPI` and `NAME-NPI` self placeholders, `<Unavail>` and `Unknown` shown as blank. The group practice is the master file's as is. An optional claims tie-break (`PD_REFERENCE_CLAIMS_TIEBREAK=1`, off by default) moves a clinician whose master-file group does not match their billing organization or parent by name to the CMS reassignment group that does (`affiliation_source = 'claims'`); after changing it run `ref-overlay`, `group-summary` and `phase5 --referrals-only`. The affiliation is always on the profile, blank when the master file has none. `ref-compare` reports coverage and how often the claims billing organization (Bills under) agrees with the group practice by name. Both read the reference server only through the read-only paged session.

## Phase 2

Activity, panel, and top codes for the mart's current 12-month window (`pd_refresh_state`, shown by `/v1/mart`). Reads `az.pat_dt` / `azal.diagnosis` / `azal.procd`. Writes only `az_pd`.

Wait until Phase 1 `--download` has finished (it updates `pd_provider`). Then:

```
python -m provider_directory.cli phase2
python -m provider_directory.cli get --last-name Smith --limit 3
```

This scan is large (12 months of `pat_dt`). Galera cannot replicate one giant transaction (`Maximum writeset size exceeded`), so Phase 2 commits by month and hash bucket. You should see `phase2 window YYYYMM bucket N` lines as it goes.

Keep Phase 2 staging (`pd_stg_visit`, `pd_stg_window_claim`) around for Phase 3.

`phase2 --top-codes-only` re-ranks the top 5 diagnoses, top 5 procedures and the top 5 **primary procedures** (codes whose strict physician work RVU is above 1, leaving out visit-type services: E/M and telehealth visits, eye exams, psychiatry, therapy evaluations, wellness G codes, plus Category II and lab-panel codes; the list is `settings.PRIMARY_PROCEDURE_EXCLUDED_*`) from the existing `pd_stg_visit`, overlays only those columns and rebuilds `pd_group_practice`. No claims are read; minutes. When fewer than five of a clinician's codes pass the strict rule, the open slots are filled from their other billed codes (office visits included), the final five are ranked by visits, and `primary_procedures_filled` is set so the page can caption the list. GET returns each top diagnosis, procedure and primary procedure with its 12-month visit count (`*_count`); there is no share field.

```
python -m provider_directory.cli phase2 --state AZ_CMS --top-codes-only
```

## Phase 3

Claims-weighted practice locations. Site names come from `az.sl` only: `sl_dba_name`, else `sl_name`, else `sl_common_name`, else street and city; blank, `Unknown` and `LAST, FIRST` values fall through, so no site is ever named Unknown. `provider_facility_npi` is no longer read. The site's service type (`work_type`, label "Service type") is `az.sl.im_specialty_rollup` as is; the CMS place-of-service code is not mapped. A site whose only name is the clinician's own LAST, FIRST keeps that name with " Office" appended (Phase 3 and the Phase 4 polish), and only blank or Unknown names fall to street and city. One site per visit from the modal `sl_code` on Phase 2 staging, clustered by street+ZIP (suite variants collapse), ranked to five sites, PDC phone overlaid when ZIP+street or ZIP+city match. Does not rescan `az.pat_dt` and does not write NPPES/PDC streets over claims sites.

```
python -m provider_directory.cli phase3
python -m provider_directory.cli get 1952863797
```

Re-run `phase3` after any `phase2` rebuild. Referring-only NPIs have no rendered visits, so they will have empty `practices`.

Phase 3 JSON that looks healthy: `visit_sites` in the tens of millions, `practice_rows` around 2 sites per active renderer, `phones_overlaid` well below practice_rows (PDC is Medicare-enrolled only).

## Phase 4

Work RVU from **every billed procedure line**: a 12-month `pat_dt` scan (one statement per month × `pat_id` bucket) counts lines per NPI, location and code into `pd_stg_npi_px_line`, crediting each line to its own rendering NPI; each 5-character CPT/HCPCS line is then priced on `azal.procd` with the strict physician work RVU (`WORK_RVU` when at least 0.05, else total − PE − MP, non-facility then facility; never `nf_total_rvu`), Category II codes excluded. Each clinician's figures then follow the primary-procedure rule (`RVU_FILTER_FOLLOWS_PRIMARY_PROCEDURES`): lines at or below 1 work RVU are left out, unless the clinician has fewer than five procedures above 1 (`primary_procedures_filled`, the office-visit practices), in which case every priced line counts; `wrvu_filter_applied` records which rule applied, and the prior year and specialty benchmarks use the same per-clinician rule. `wrvu_total` is the sum, `wrvu_procedure_count` the counted lines, `wrvu_average` the average work RVU per procedure. Phase 2 top codes must run before Phase 4 RVU so the flag is set. Payer mix from `az.dash_physician_payor_all` (on `AZ_CMS` from `az_cms.pat_dt` × the `payor` lookup instead, visit-weighted, because the blend's summary table only carries native claims; that is a second 12-month `pat_dt` scan), the clinician's group practice from the master reference copy (`pd_src_physician.group_prac_code`, the CMS group PAC ID, into `primary_organization_id` / `_name`; the group pages key on it), the organization the clinician bills under in claims from `az.physician_primary_affiliation` (`billing_organization_id` / `_name`, plus `primary_organization_npi` and the parent), the nested group list from the CMS reassignment copy `pd_src_group_practice` plus the master-file group, the hidden per-organization visit list, and site-of-care `work_type` labels from CMS POS plus warehouse rollup (the same rule Phase 3 uses). Phase 4 also rewrites any practice name that is blank, Unknown or `LAST, FIRST` to street+city so a site is never shown as Unknown or as another clinician's name. Payer classes are `is_payor_code` 1 Medicare FFS, 2 Medicaid, 3 Commercial, 4 HMO, 6 Medicare Advantage and 7 Medicare Other, each its own `visits_percent_*` column over the sum of those six; class 5 Other is excluded from the denominator. Top 5 payers are commercial parents only.

`phase4 --rvu-only` runs just the work RVU scan and overlays (provider totals, site RVU, group summary) when the pricing rule or fee schedule changes.

```
python -m provider_directory.cli phase4 --state AZ_CMS --rvu-only
```

```
python -m provider_directory.cli phase4
python -m provider_directory.cli get 1952863797
```

Keep Phase 2–3 staging. Re-run `phase4` after `phase2`/`phase3` rebuilds.

`phase4 --payers-only` refills the payer-class shares and top 5 commercial payers from the `pd_stg_npi_payor` staging the last full Phase 4 left behind (adds the new columns, no claims read, a few minutes), then rebuilds `pd_group_practice`. Payer classes: 1 Medicare FFS, 2 Medicaid, 3 Commercial, 4 HMO, 6 Medicare Advantage, 7 Medicare Other; class 5 Other is left out of the percents.

```
python -m provider_directory.cli phase4 --state AZ_CMS --payers-only
```

## Phase 5

Referrals both directions, from `az.dash_physician_referrals_to_rendering` (`REFERRAL_SOURCE = "implied"`, the default: the warehouse infers the pair from visit order) or from the referring NPI recorded on each window claim line in `pd_stg_window_claim` (`PD_REFERRAL_SOURCE=claims`: the clinician the billing office named, distinct patients over the window, organization NPIs dropped by the Type 1 join; needs Phase 2's staging). Switching sources needs `phase5 --referrals-only --rebuild-edges`; `/v1/mart` reports `referral_source` and the catalog wording follows it. Day-of-week mix from `az.pat_dt.service_end_date`, prior-year work RVU on the same line-level basis as Phase 4 (the prior-year lines are cached in `pd_stg_npi_px_line_prior`; pricing is redone each run), and in-state specialty work RVU benchmarks. Peers are same-specialty clinicians with a practice site in the market state and at least `BENCHMARK_MIN_VISITS` (50) visits. The main strip is average work RVU per procedure: the cohort average, p25 / median / p75 of the clinicians' averages, and the clinician's percentile of their average (`wrvu_average_specialty_percentile`); the same statistics on per-provider totals are kept as secondary figures. Nested `referrals` on `get` (`in` = who sends to this NPI, `out` = who this NPI sends to). Top 5 peers per direction by summed monthly `patient_count`, after leaving out peers in the same group practice or the same claims billing organization and peers whose specialty matches `settings.REFERRAL_EXCLUDED_SPECIALTY_REGEXP` (pathology, diagnostic radiology, anesthesiology, lab). Dummy NPIs 0/4 and self-referrals dropped. Type 1 spine only. The edge table `pd_stg_referral_edge` is cached across runs; `phase5 --referrals-only` re-ranks from it in minutes after a change to the exclusions or peer count, and `--rebuild-edges` refills it from the warehouse first.

Day-of-week is visit grain, by encounter date. Site DOW uses Phase 3 `pd_stg_visit_site`. Visit dates and the prior-year billed lines are cached (`pd_stg_visit_date`, `pd_stg_npi_px_line_prior`) and reused on reruns; the prior-year work RVU is re-priced from the cached lines each run. Drop those two tables if you need a full rebuild.

```
python -m provider_directory.cli phase5
python -m provider_directory.cli get 1952863797
```

Keep Phase 2–4 staging. First run scans `pat_dt` for dates and the prior year — expect Phase 2-like runtime. Referral/benchmark overlays are cheaper. Do not rerun Phase 2–4 first.

## Phase 6

Monthly incremental mart. `get` / `search` already read `az_pd` only — they never scan `az.pat_dt`. Phase 6 adds search indexes, a `period_code` index on window-claim staging (so dropping a month is not a 49M-row table scan), and a `pd_refresh_state` watermark. When the warehouse grows past the 2-month lag, `--slide` adds only the new month(s) of `pat_dt`, deletes the month that fell out of the 12-month window, then rebuilds visits/locations/analytics/complete from the updated staging.

Do **not** ALTER `az` or `azal`. Copy `provider_directory/` into Analysis Scripts after pulling these changes.

```
python -m provider_directory.cli phase6
python -m provider_directory.cli get 1952863797
```

`phase6` without `--slide` is safe to run now. Building `pd_stg_window_claim.idx_period` on ~49M rows can take a while on Galera; `--skip-staging-indexes` only adds the small `pd_provider` search indexes.

If JSON shows `"slide_available": true`, a later usable month exists (warehouse max minus 2 months is past the current `window_end`). Then:

```
python -m provider_directory.cli phase6 --slide
```

`--slide` does **not** rescan the 11 months that stay in the window. It **does** rebuild Phase 3–5 from staging (locations cannot reuse stale `pd_stg_visit_site`; Phase 5 date/prior-wRVU caches are dropped). After a slide, later `phase2`–`phase5` runs use the window stored in `pd_refresh_state`.

If the warehouse has no month past the current window plus the 2-month lag, the usable end stays where it is and `--slide` is a no-op after indexes.

## Phase 7 — HTTP API for .NET

The UI lives in your .NET app. This process is a local API: lookup/search from `az_pd`, plus `POST /v1/jobs/phaseN` so NSSM/IIS do not wait hours on a rebuild.

```
pip install -r requirements.txt
python -m provider_directory.cli serve
```

Defaults: `http://127.0.0.1:8080` (loopback only), OpenAPI at `/docs`. Set `PD_API_KEY` and send it as `X-API-Key` (or `Authorization: Bearer`). `PD_CORS_ORIGINS` is a comma list if the browser talks to this API directly; a server-side .NET `HttpClient` does not need CORS.

| Method | Path | What |
| --- | --- | --- |
| GET | `/health` | Process up (no API key). Use this for NSSM / probes. |
| GET | `/v1/mart?state=` | Frozen window, warehouse max, running job (`AZ` → `az_pd`) |
| GET | `/v1/providers/{npi}?state=` | Full profile + `practices` + `group_practices` + `hospital_affiliations` + `referrals` |
| GET | `/v1/providers?state=&limit=&offset=` | Picker dump, slim rows, max 500 per page. Filters include `organization_id` + `membership` (`primary` / `any`) and `zip` |
| GET | `/v1/group-practices?state=&limit=&offset=` | Group-practice dump (sums of Type 1s by group practice, the CMS group PAC ID from the master reference file). `group_state` defaults to `market` (groups whose members mostly practise in the state); `any` lifts it. Max 500 per page |
| GET | `/v1/group-practices/{organization_id}?state=` | Group profile (same tabs as a provider, member-aggregated) |
| GET | `/v1/field-help?state=&dev=` | Label + hover text for every field, by scope (`provider`, `group`, `practice`, `group_practice`, `hospital_affiliation`, `referral`, `utilization`, plus `provider_filter`, `group_filter`, `section` and `group_section` for controls and headings) and JSON key. `dev=true` adds the developer tier: formula, `caveats[]`, `derived_from[]` (lineage to other fields) and `tables[]` (kind warehouse / mart / cms, `external` flag, `upstream` flag, `phase`, CMS dataset `url`); set `PD_DEV_HELP_KEY` and send it as `X-Dev-Key` to gate it. Static per deployment; cache it |
| POST | `/v1/jobs/phase1` … `phase6` | 202 + `Location`. Body optional: `{state, …}` plus phase1 `{download, skip_pdc, skip_nppes}`, phase6 `{slide, skip_staging_indexes}` |
| GET | `/v1/jobs/{id}` | `queued` / `running` / `succeeded` / `failed` |
| GET | `/v1/jobs` | Recent jobs |

The group-practice dump and group profile read `pd_group_practice`, one precomputed row per group practice (CMS group PAC ID) per member scope (`all` / `active` / `inactive`), so filter, sort and page are indexed reads instead of a 858K-row aggregation per request. It is rebuilt at the end of `phase2`, `phase4`, `overlay-cms` and `phase6` (a few seconds). After pulling this change run `phase6 --state AZ_CMS` once to fill it; until then the dump falls back to the live aggregation.

Field help: the UI should fetch `/v1/field-help` once per session and bind each field's tooltip by scope + key. The same text prints from `python -m provider_directory.cli field-help --state AZ_CMS [--dev] [--format md|json]` for a handout. `{window}` in the text resolves to the mart's current window.

One phase job at a time (409 if another is running). `workers=1` is required so job state stays in this process. Poll `GET /v1/jobs/{id}` — do not use a 100-second HTTP timeout on `POST /v1/jobs/phase2`.

NSSM (App directory = Analysis Scripts, same `.env` as the CLI):

```
nssm install PdApi "C:\Users\jluna\Documents\Analysis Scripts\.venv\Scripts\python.exe"
nssm set PdApi AppDirectory "C:\Users\jluna\Documents\Analysis Scripts"
nssm set PdApi AppParameters "-m provider_directory.cli serve --host 127.0.0.1 --port 8080"
nssm set PdApi AppStdout "C:\Users\jluna\Documents\Analysis Scripts\logs\pd-api.out.log"
nssm set PdApi AppStderr "C:\Users\jluna\Documents\Analysis Scripts\logs\pd-api.err.log"
nssm set PdApi AppRotateFiles 1
```

Set `PD_API_KEY` in the service environment (or `.env`). Bind `127.0.0.1` unless you put a reverse proxy in front.

.NET (server-side). JSON fields are **snake_case** (`last_name`, `visits_total`, `practices`), matching the CLI `get` payload:

```csharp
var json = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
var client = new HttpClient { BaseAddress = new Uri("http://127.0.0.1:8080") };
client.DefaultRequestHeaders.Add("X-API-Key", config["PdApiKey"]);

var provider = await client.GetFromJsonAsync<JsonElement>("/v1/providers/1952863797", json);
var search = await client.GetFromJsonAsync<JsonElement>(
    "/v1/providers?last_name=Smith&active=true&min_visits=1&limit=25", json);
var groups = await client.GetFromJsonAsync<JsonElement>(
    "/v1/group-practices?min_visits=1&limit=25", json);

var started = await client.PostAsJsonAsync("/v1/jobs/phase6", new { slide = false });
var jobUrl = started.Headers.Location; // /v1/jobs/{guid}
```

Copy `provider_directory/` into Analysis Scripts after pulling these changes, then `pip install -r requirements.txt` in that venv so FastAPI/uvicorn land.

## Extras pack (no Phase 2)

Competitive extras overlay onto the existing mart. Does **not** rescan `az.pat_dt`, does **not** TRUNCATE `pd_provider`, and does **not** drop Phase 2–5 staging.

```
python -m provider_directory.cli extras --state AZ --skip-open-payments
python -m provider_directory.cli extras --state AZ --reload-pdc
python -m provider_directory.cli extras --state AZ --download
python -m provider_directory.cli extras --state AZ --skip-mips --skip-utilization --open-payments-kinds ownership
python -m provider_directory.cli extras --state AZ --open-payments-overlay-only
python -m provider_directory.cli get --state AZ 1952863797
```

Default `extras` overlays group size, PDC telehealth Y/N, secondary specialties (if `sec_spec_*` already on `cms_pdc_clinician`), new vs established E/M from `pd_stg_visit.px`, and POS mix (office / HOPD / ASC / ED / telehealth POS 02+10 / inpatient POS 21 / lab POS 81) from `pd_stg_visit_site` + `{st}.sl`. `--state AZ` is the current mart. `--reload-pdc` TRUNCATEs **only** `cms_pdc_clinician` and reloads the cached DAC CSV so `Sec_spec_1`–`4` land. `--download` fetches MIPS (`a174-a962`), Care Compare utilization (`n0yb-util`), and Open Payments CSVs into `data/cms` (latest complete program year; general file is huge; reruns reuse cached files; shared across states). `--year 2024` pins Open Payments. `--skip-mips` / `--skip-utilization` / `--skip-open-payments` skip those files. `--open-payments-kinds ownership` re-parses only the ownership CSV (uses `Physician_NPI` and `Value_of_Interest`) without rereading the 9GB general file. `--open-payments-overlay-only` rewrites `pd_provider` from `cms_open_payments` without reading CSVs. Missing Open Payments kinds stay null.

Weekend / after-hours is UI-only (`visits_percent_saturday` / `sunday` already exist). HTTP GET includes `group_size`, `telehealth_offered`, `secondary_specialty_*`, E/M and POS percents, and `mips_*`. It omits `open_payments_*` and Care Compare `utilization[]` (practice profile). CLI `get` still prints those omitted columns. Claims sites stay on `practices[]`.

.NET UI spec: `docs/ui-agent-spec.md`.

## When new data lands

Do **not** `phase1`. Clocks, one mart per `--state`. Check first with `--dry-run`.

| Clock | When | Command |
| --- | --- | --- |
| Claims | Warehouse has a new month past the 2-month lag (`slide_available`) | `python -m provider_directory.cli sync --state AZ` or `scripts/sync-claims.ps1` (also upserts new Type 1 NPIs from `{st}.physician`) |
| Roster | New physicians on `{st}.physician` | Implied by claims `sync`. Or `sync --state AZ --spine` only. Never `phase1`. |
| CMS identity | New DAC / NPPES already in `data/cms` | `sync --state AZ --cms` (`--reload-pdc` only if clinician DAC must be reloaded) |
| Open Payments | CMS publish (~June / January) | `sync --state AZ --open-payments` (reuses cache; general file is huge) |
| MIPS / utilization | Care Compare yearly | `sync --state AZ --mips --utilization` |

Default `sync` upserts the Type 1 roster (insert missing, refresh name/specialty; never truncates), then is a no-op on the claims window if it is already current; otherwise `phase6 --slide` then extras for E/M + POS only (does not reread Open Payments). Copy `scripts/` into Analysis Scripts if you want Task Scheduler to call the `.ps1` files. UI still has no phase buttons.



## Field help fixtures

`docs/fixtures/field-help.AZ_CMS.json` (client tier) and `field-help.AZ_CMS.dev.json` (developer tier) are the exact bodies `GET /v1/field-help?state=AZ_CMS[&dev=true]` returns, with the window pinned to Nov 2024 to Oct 2025. UI work can run against them without the API. Regenerate after editing `provider_directory/field_help.py`:

```
python -m provider_directory.cli field-help --state AZ_CMS --dev --format json --window-start 202411 --window-end 202510 --out docs/fixtures/field-help.AZ_CMS.dev.json
python -m provider_directory.cli field-help --state AZ_CMS --format json --window-start 202411 --window-end 202510 --out docs/fixtures/field-help.AZ_CMS.json
```
