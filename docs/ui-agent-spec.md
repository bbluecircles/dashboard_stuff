# .NET UI agent spec — provider directory

Hand this to the UI agent. The Python mart is already live. The UI is a **.NET app on IIS**. FastAPI is a loopback lookup service, not a dashboard.

Do **not** rerun `phase1` (it TRUNCATEs `pd_provider`). Do **not** put Phase 1–6 job buttons in the UI. Do **not** scan `pat_dt` from the app.

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

Every lookup request must send **`state`** (two-letter USPS, default `AZ`). That selects the mart: `AZ` → `az` / `azal` / `az_pd`, `TX` → `tx` / `txal` / `tx_pd`. Do not hard-code Arizona in the UI.

```
GET /v1/mart?state=AZ
GET /v1/providers?state=AZ&limit=50&offset=0
GET /v1/providers/1952863797?state=AZ
```

## Routes the UI should call

| Method | Path | Use |
| --- | --- | --- |
| GET | `/health` | Process up. No API key. NSSM / probes. |
| GET | `/v1/mart?state=` | Frozen window, warehouse max, `slide_available`. Banner **as of** `window_end`. |
| GET | `/v1/providers?state=&limit=&offset=` | **Picker dump** — slim rows, paged. Max `limit` 500. |
| GET | `/v1/providers/{npi}?state=` | Full profile after a row is selected |

Do **not** call `POST /v1/jobs/phaseN` from the UI. Phase jobs stay CLI / NSSM.

## Picker modal (data dump)

The spec is a table of providers, not a typeahead-only search. The API will **not** return 500k rows in one JSON. Page it.

**How to dump without melting the browser**

- Virtualize the grid (or page 50–100 rows). Request the next page as the user scrolls.
- `limit` default 50, max 500. Use `offset` + `total` from the response.
- Default `min_visits=1` so referring-only NPIs (`visits_total = 0`) are out unless the user clears it.
- Optional filters still work on the dump: `last_name`, `specialty`, `active`, `in_system`.
- Sort is server-side: `visits_total` desc, then `panel_size`, then name. Do not re-sort 500k in memory.
- List rows are **slim**. No `practices[]`, `referrals[]`, or `utilization[]`. Load those only on the selected NPI.

**Dump row columns** (`GET /v1/providers`)

| JSON | Table column |
| --- | --- |
| `last_name`, `first_name`, `credential` | Name |
| `primary_specialty_description` | Specialty |
| `primary_organization_name` | Organization |
| `city` / `state` | Primary practice city (site_rank 1; may be null) |
| `visits_total` | Visits |
| `in_system_provider` | In-system (PDC facility CCN) |
| `active_provider` | Active in the frozen window |
| `npi` | NPI (keep, can be a secondary column) |

`total` is the full match count (can be hundreds of thousands). Show it as “showing 1–50 of N”. Do not try to render N DOM rows.

Response also includes `state` and `mart_db` so the UI can confirm it hit the right market.

On row select, close the modal and `GET /v1/providers/{npi}?state=`.

## Product locks

- Claims live in `{st}`, lookups in `{stal}`. The API reads **`{st}_pd` only**.
- Universe: Type 1 NPIs. Visit = distinct `encounter_id`.
- Frozen window: **Aug 2023 – Jul 2024** (`202308`–`202407`) until a `phase6 --slide`. Banner copy from `/v1/mart`: **as of {window_end month}**.
- Payers 1/2/3/4/5 as locked. Dummy NPIs 0 and 4 are dropped.
- `in_system_provider` is a **CMS Provider Data Catalog facility affiliation** (hospital CCN), not a Vue roster.
- Blank phones are OK. Do not invent a phone.
- Top 3 lists stay top 3 (diagnoses, procedures, payers, referrals in and out). Practice sites stay top 5.
- Weekend / after-hours is **UI-only**: `visits_percent_saturday` and `visits_percent_sunday` are already on the provider and each practice.

## wRVU

Numbers are **total-RVU scale**, not CMS work RVU. Cardiology mean vs median is badly skewed (mean ~1317, median ~8.73). **Prefer median / percentile** (`wrvu_state_specialty_median`, `wrvu_state_specialty_p25`, `wrvu_state_specialty_p75`, `wrvu_specialty_percentile`) over `wrvu_average` / `wrvu_state_specialty_average`. Label copy is still TBD — do not call it “wRVU” as if it were physician work RVU until product names it.

## Profile (`GET /v1/providers/{npi}`)

Same payload as `python -m provider_directory.cli get --state AZ {npi}`. Hide a section when every field in it is null.

1. **Header** — name, credential, specialty, estimated age / school if present, in-system badge, primary org + parent, NPI.
2. **Where they work** — top 5 sites: name, city, work type, visit share, phone if present. Map if lat/long exist. Weekend % on each site.
3. **Volume** — visits, panel size, specialty median / p25 / p75 / percentile. Do not lead with `wrvu_average`.
4. **What they do** — top 3 dx / px (names); POS mix (office / HOPD / ASC / ED / telehealth / inpatient / lab); new vs established only if E/M counts exist. Hide a POS bucket at 0% if the others already tell the story.
5. **Who they see** — panel age bands + sex. Payer mix + top commercial parents. Hide a 0% extra payer.
6. **Who they work with** — referrals in / out, top 3 each.
7. **Access** — Mon–Sun including Sat/Sun.
8. **CMS extras** — group size, telehealth offered, secondary specialties, MIPS, Open Payments (non-null kinds only; never `$0` for a missing kind), `utilization[]`. NPs often have this block empty.

### Extras fields (null means CMS has no row, not that extras never ran)

| JSON field | Meaning | UI note |
| --- | --- | --- |
| `group_size` | CMS `num_org_mem` on the ranked PDC row | Group practice size, not claims panel |
| `telehealth_offered` | PDC `telehlth` Y/N | Boolean. Not visit share. |
| `secondary_specialty_1` … `_4` | PDC `Sec_spec_1`–`4` | Hide blanks |
| `visits_new_patient` / `visits_established` | CPT 99202–05 vs 99212–15 | Office E/M only |
| `visits_percent_new_patient` | New / (new + established) | Null if no E/M in those buckets |
| `visits_percent_office` | POS 11 | |
| `visits_percent_hopd` | POS 19 + 22 | |
| `visits_percent_asc` | POS 24 | |
| `visits_percent_ed` | POS 23 | |
| `visits_percent_telehealth` | POS 02 + 10 | |
| `visits_percent_inpatient` | POS 21, or short-term acute hospital | Sean Smith smoke is mostly this |
| `visits_percent_lab` | POS 81, or laboratory work_type | |
| `visits_percent_other_pos` | Everything else | Hide if named buckets sum to ~100 |
| `mips_final_score` / `mips_quality_score` | PDC clinician MIPS | |
| `open_payments_year` | Program year summed | Currently 2025 |
| `open_payments_general_total` | General $ | Null if none; never show `$0` for a missing kind |
| `open_payments_research_total` | Research $ | Null if no research rows |
| `open_payments_ownership_total` | Ownership $ | Null if none |
| `open_payments_count` | Payment row count | |
| `utilization[]` | Care Compare procedure categories | Often empty for NPs and low-volume NPIs |

## Smoke NPIs (`state=AZ`)

- **Sean Smith `1952863797`**: `in_system_provider: true`, Mayo, 6 visits, phones may be null. Open Payments and `utilization[]` are null.
- **Lori Schott `1609236967`**: Open Payments 2025 general ~$38, research/ownership **null** (not `0.0`), count 2. High OTP visit volume. Group size / MIPS / utilization may be null (NP).

## Out of v1 UI

- Catchment zips, claims-based specialty, attending vs operating, telehealth **modifier** visit %
- Phase job controls
- Editing mart tables
- Geocoding beyond lat/long already on `practices`
- Loading the entire spine in one HTTP call

## After code drops

Copy `provider_directory/` into `C:\Users\jluna\Documents\Analysis Scripts`, restart NSSM. Then:

```
python -m provider_directory.cli get --state AZ 1952863797
python -m provider_directory.cli get --state AZ --min-visits 1 --limit 5
```

`--state` selects `{st}` / `{st}al` / `{st}_pd`. CMS national files in `data/cms` are shared. Never `phase1` from the UI.
