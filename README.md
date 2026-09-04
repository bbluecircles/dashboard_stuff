# Arizona provider directory

Python mart first. FastAPI later wraps the same functions (`get_provider`, `run_phase1`).

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

`in_system_provider` is filled from PDC facility affiliations (hospital CCN), not a Vue roster. After Phase 1 has already loaded `cms_pdc_facility_affil`, refresh the flag without rebuilding the spine:

```
python -m provider_directory.cli overlay-cms
python -m provider_directory.cli get 1952863797
```

Do **not** rerun `phase1` — that TRUNCATEs `pd_provider` and you would have to rerun 2–5.

Same DB env as `db_snapshot.py`: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`. `--state AZ` selects `az` / `azal` / `az_pd` (and `TX` → `tx` / `txal` / `tx_pd`). Optional env: `PD_MARKET_STATE` (default `AZ`). `PD_CLAIMS_DB` / `PD_LOOKUP_DB` / `PD_MART_DB` remain the import defaults when code does not pass `--state`.

## Phase 2

Activity, panel, and top codes for `period_code` 202308–202407. Reads `az.pat_dt` / `azal.diagnosis` / `azal.procd`. Writes only `az_pd`.

Wait until Phase 1 `--download` has finished (it updates `pd_provider`). Then:

```
python -m provider_directory.cli phase2
python -m provider_directory.cli get --last-name Smith --limit 3
```

This scan is large (12 months of `pat_dt`). Galera cannot replicate one giant transaction (`Maximum writeset size exceeded`), so Phase 2 commits by month and hash bucket. You should see `phase2 window YYYYMM bucket N` lines as it goes.

Keep Phase 2 staging (`pd_stg_visit`, `pd_stg_window_claim`) around for Phase 3.

## Phase 3

Claims-weighted practice locations. One site per visit from the modal `sl_code` on Phase 2 staging, clustered by street+ZIP (suite variants collapse), ranked to five sites, PDC phone overlaid when ZIP+street or ZIP+city match. Does not rescan `az.pat_dt` and does not write NPPES/PDC streets over claims sites.

```
python -m provider_directory.cli phase3
python -m provider_directory.cli get 1952863797
```

Re-run `phase3` after any `phase2` rebuild. Referring-only NPIs have no rendered visits, so they will have empty `practices`.

Phase 3 JSON that looks healthy: `visit_sites` in the tens of millions, `practice_rows` around 2 sites per active renderer, `phones_overlaid` well below practice_rows (PDC is Medicare-enrolled only).

## Phase 4

wRVU (5-character CPT/HCPCS work procedure × a plausible physician work RVU from `azal.procd`: `WORK_RVU` when it is at least 0.05, otherwise total − PE − MP), payer mix from `az.dash_physician_payor_all`, primary billing org from `az.physician_primary_affiliation`, and Trilliant-style `work_type` labels. Phase 4 also replaces Type 1 `LAST, FIRST` practice names with street+city so another clinician's NPI is not shown as the site. Does not rescan `az.pat_dt`. Other / is_payor 5 is excluded from the four payer percents. Top 3 payers are commercial parents only.

```
python -m provider_directory.cli phase4
python -m provider_directory.cli get 1952863797
```

Keep Phase 2–3 staging. Re-run `phase4` after `phase2`/`phase3` rebuilds.

## Phase 5

Referrals both directions from `az.dash_physician_referrals_to_rendering`, day-of-week mix from `az.pat_dt.service_end_date`, prior-year wRVU (202208–202307, same formula as Phase 4), and state-specialty wRVU benchmarks (average / p25 / median / p75 / percentile among NPIs with the same taxonomy). Nested `referrals` on `get` (`in` = who sends to this NPI, `out` = who this NPI sends to). Top 3 peers per direction by summed monthly `patient_count`. Dummy NPIs 0/4 and self-referrals dropped. Type 1 spine only.

Day-of-week is visit grain (encounter date), not `period_code`. Site DOW uses Phase 3 `pd_stg_visit_site`. Visit dates and prior-year wRVU are cached (`pd_stg_visit_date`, `pd_stg_npi_wrvu_prior`) and reused on reruns. Drop those two tables if you need a full rebuild.

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

If the warehouse is still 202409, usable end stays 202407 and `--slide` is a no-op after indexes.

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
| GET | `/v1/providers/{npi}?state=` | Full profile + `practices` + `referrals` |
| GET | `/v1/providers?state=&limit=&offset=` | Picker dump, slim rows, max 500 per page |
| POST | `/v1/jobs/phase1` … `phase6` | 202 + `Location`. Body optional: `{state, …}` plus phase1 `{download, skip_pdc, skip_nppes}`, phase6 `{slide, skip_staging_indexes}` |
| GET | `/v1/jobs/{id}` | `queued` / `running` / `succeeded` / `failed` |
| GET | `/v1/jobs` | Recent jobs |

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

Default `extras` overlays group size, PDC telehealth Y/N, secondary specialties (if `sec_spec_*` already on `cms_pdc_clinician`), new vs established E/M from `pd_stg_visit.px`, and POS mix (office / HOPD / ASC / ED / telehealth POS 02+10 / inpatient POS 21 / lab POS 81) from `pd_stg_visit_site` + `{st}.sl`. `--state AZ` is the current mart. `--reload-pdc` TRUNCATEs **only** `cms_pdc_clinician` and reloads the cached DAC CSV so `Sec_spec_1`–`4` land. `--download` fetches MIPS (`a174-a962`), Care Compare utilization (`n0yb-util`), and Open Payments CSVs into `data/cms` (latest complete program year; general file is huge; reruns reuse cached files; shared across states). `--year 2024` pins Open Payments. `--skip-mips` / `--skip-utilization` / `--skip-open-payments` skip those files. `--open-payments-kinds ownership` re-parses only the ownership CSV (uses `Physician_NPI` and `Value_of_Interest`) without rereading the 9GB general file. `--open-payments-overlay-only` rewrites `pd_provider` from `cms_open_payments` without reading CSVs. Missing Open Payments kinds are null, not `$0`.

Weekend / after-hours is UI-only (`visits_percent_saturday` / `sunday` already exist). GET grows `group_size`, `telehealth_offered`, `secondary_specialty_*`, E/M and POS percents, `mips_*`, `open_payments_*`, and nested `utilization`.

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


