# .NET UI agent spec — Arizona provider directory

Hand this to the UI agent. The Python mart is already live. The UI is a **.NET app on IIS**. FastAPI is a loopback lookup service, not a dashboard.

Do **not** rerun `phase1` (it TRUNCATEs `pd_provider`). Do **not** put Phase 1–6 job buttons in the UI. Do **not** scan `az.pat_dt` from the app.

## Runtime

| Piece | Where |
| --- | --- |
| UI | .NET on IIS |
| API | FastAPI on **loopback only**: `http://127.0.0.1:8080` |
| Process | NSSM service `PdApi`, one uvicorn worker |
| Auth | `PD_API_KEY` sent as `X-API-Key` (or `Authorization: Bearer`) |
| JSON | **snake_case** (`last_name`, `visits_total`, `practices`) |

OpenAPI: `http://127.0.0.1:8080/docs` when `PD_API_DOCS` is not `0`.

```csharp
var json = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
var client = new HttpClient { BaseAddress = new Uri("http://127.0.0.1:8080") };
client.DefaultRequestHeaders.Add("X-API-Key", config["PdApiKey"]);
```

CORS is only needed if the browser talks to FastAPI directly. A server-side .NET `HttpClient` does not need CORS.

## Routes the UI should call

| Method | Path | Use |
| --- | --- | --- |
| GET | `/health` | Process up. No API key. NSSM / probes. |
| GET | `/v1/mart` | Frozen window, warehouse max, `slide_available`, running job |
| GET | `/v1/providers/{npi}` | Full profile |
| GET | `/v1/providers?last_name=&specialty=&active=&min_visits=&in_system=&limit=&offset=` | Search (max 100) |

Do **not** call `POST /v1/jobs/phaseN` from the UI. Phase jobs stay CLI / NSSM.

## Product locks

- Claims live in `az`, lookups in `azal`. The API reads **`az_pd` only**.
- Universe: Type 1 NPIs. Visit = distinct `encounter_id`.
- Frozen window: **Aug 2023 – Jul 2024** (`202308`–`202407`). Banner copy: **as of Jul 2024**.
- Payers 1/2/3/4/5 as locked. Dummy NPIs 0 and 4 are dropped.
- `in_system_provider` is a **CMS Provider Data Catalog facility affiliation** (hospital CCN), not a Vue roster. `true` if the NPI is in `cms_pdc_facility_affil`. Nested network filter later can use CCNs; they are not on the GET payload today.
- Blank phones are OK. Do not invent a phone.
- Search can default to Arizona later; other states later. v1 search is last name / specialty / active / min visits / in-system.
- Single-physician profile plus search. Top 3 lists stay top 3 (diagnoses, procedures, payers, referrals in and out). Practice sites stay top 5.
- Weekend / after-hours is **UI-only**: `visits_percent_saturday` and `visits_percent_sunday` are already on the provider and each practice. Surface them as access, not as a new API field.

## wRVU

Numbers are **total-RVU scale**, not CMS work RVU. Cardiology mean vs median is badly skewed (mean ~1317, median ~8.73). **Prefer median / percentile** (`wrvu_state_specialty_median`, `wrvu_state_specialty_p25`, `wrvu_state_specialty_p75`, `wrvu_specialty_percentile`) over `wrvu_average` / `wrvu_state_specialty_average`. Label copy is still TBD — do not call it “wRVU” as if it were physician work RVU until product names it.

## GET `/v1/providers/{npi}` shape

Same payload as `python -m provider_directory.cli get {npi}`.

Top-level identity and activity (existing):

- Name, credential, gender, medical school, graduation year, estimated age
- `primary_specialty_code` / `primary_specialty_description` / `specialty_classification`
- `in_system_provider`, `active_provider`, `visits_total`, panel mix
- Top 3 diagnoses and procedures (code + name)
- Payer mix percents + top 3 commercial payer names
- Primary organization
- Day-of-week percents (Mon–Sun)
- wRVU totals + prior year + specialty benchmarks
- `practices[]` ranked `site_rank` 1–5 (phone may be null)
- `referrals[]` with `direction` `in` | `out`, `peer_rank` 1–3

### Extras pack (null until `python -m provider_directory.cli extras` has been run)

| JSON field | Meaning | UI note |
| --- | --- | --- |
| `group_size` | CMS `num_org_mem` on the ranked PDC row | Group practice size, not claims panel |
| `telehealth_offered` | PDC `telehlth` Y/N | Boolean. Not visit share. |
| `secondary_specialty_1` … `_4` | PDC `Sec_spec_1`–`4` | Hide blanks |
| `visits_new_patient` / `visits_established` | CPT 99202–05 vs 99212–15 on `pd_stg_visit.px` | Office E/M only |
| `visits_percent_new_patient` | New / (new + established) | Null if no E/M in those buckets |
| `visits_percent_office` | POS 11 | Claims site mix |
| `visits_percent_hopd` | POS 19 + 22 | Off-campus + hospital outpatient |
| `visits_percent_asc` | POS 24 | |
| `visits_percent_ed` | POS 23 | |
| `visits_percent_telehealth` | POS 02 + 10 | Visit % without a `pat_dt` rescan |
| `visits_percent_other_pos` | Everything else, including null POS | |
| `mips_final_score` / `mips_quality_score` | PDC clinician overall MIPS | Prefer org PAC match, else max score |
| `open_payments_year` | Program year summed | Latest complete CMS year unless `--year` |
| `open_payments_general_total` | General (non-research) $ | Covered recipient NPI only |
| `open_payments_research_total` | Research $ | |
| `open_payments_ownership_total` | Ownership $ | |
| `open_payments_count` | Payment row count | |
| `utilization[]` | Care Compare procedure categories | `rk`, `procedure_category`, `count_label` (may be `1-10`), `percentile` |

Null extras are expected before the extras overlay. Do not show “0” for missing MIPS or Open Payments.

## Search

`GET /v1/providers?last_name=Smith&active=true&min_visits=1&limit=25`

Returns `{ "items": [ ...same spine fields... ], "total": N }`. Items include nested `practices`, `referrals`, and `utilization`. Sort is `visits_total` desc, then `panel_size`, then name.

Smoke NPI: **Sean Smith `1952863797`**. Expect `in_system_provider: true`, Mayo practice, small visit count, phones may be null.

## Out of v1 UI

- Catchment zips, claims-based specialty, attending vs operating, telehealth **modifier** visit %
- Phase job controls
- Editing mart tables
- Geocoding beyond lat/long already on `practices`

## After code drops

Copy `provider_directory/` into `C:\Users\jluna\Documents\Analysis Scripts`, `pip install -r requirements.txt` if needed, restart NSSM. Then:

```
python -m provider_directory.cli extras --skip-open-payments
python -m provider_directory.cli get 1952863797
```

`--skip-open-payments` overlays group size, telehealth Y/N, secondary specialties (after `--reload-pdc` if those columns were never loaded), E/M, POS mix, and MIPS/utilization if those CSVs are already in `data/cms`. Open Payments `--download` streams large CMS CSVs (general file is huge). Never `phase1`.
