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
GET /v1/group-practices?state=AZ&min_visits=1&limit=50&offset=0
GET /v1/group-practices/{organization_id}?state=AZ
GET /v1/providers?state=AZ&organization_id={id}&min_visits=1
```

## Routes the UI should call

| Method | Path | Use |
| --- | --- | --- |
| GET | `/health` | Process up. No API key. NSSM / probes. |
| GET | `/v1/mart?state=` | Frozen window, warehouse max, `slide_available`. Banner **as of** `window_end`. |
| GET | `/v1/providers?state=&limit=&offset=` | **Picker dump** — slim rows, paged. Max `limit` 500. |
| GET | `/v1/providers/{npi}?state=` | Full profile after a row is selected |
| GET | `/v1/group-practices?state=&limit=&offset=` | **Group-practice dump** — slim rows, paged. Same paging rules. |
| GET | `/v1/group-practices/{organization_id}?state=` | One group row after a group is selected |

Do **not** call `POST /v1/jobs/phaseN` from the UI. Phase jobs stay CLI / NSSM.

## Picker modal (data dump)

The spec is a table of providers, not a typeahead-only search. The API will **not** return 500k rows in one JSON. Page it.

**How to dump without melting the browser**

- Virtualize the grid (or page 50–100 rows). Request the next page as the user scrolls.
- `limit` default 50, max 500. Use `offset` + `total` from the response.
- Default `min_visits=1` so referring-only NPIs (`visits_total = 0`) are out unless the user clears it. Optional `max_visits` is an upper bound. Exact visit count is `min_visits` = `max_visits`; do not add a separate equals control.
- Optional filters: `last_name`, `specialty`, `organization` (contains on `primary_organization_name`), `city` (contains on primary practice city, site_rank 1), `active`, `in_system`, `min_visits`, `max_visits`.
- Sort is server-side: `visits_total` desc, then `panel_size`, then name. Do not re-sort 500k in memory.
- List rows are **slim**. No `practices[]`, `group_practices[]`, `hospital_affiliations[]`, `referrals[]`, or `utilization[]`. Load those only on the selected NPI.

**Dump row columns** (`GET /v1/providers`)

| JSON | Table column |
| --- | --- |
| `last_name`, `first_name`, `credential` | Name |
| `gender` | Provider gender (`M` / `F`; null if no PDC/NPPES row). Not patient panel sex. |
| `primary_specialty_description` | Specialty |
| `primary_organization_name` | Organization |
| `city` / `state` | Primary practice city (site_rank 1; may be null) |
| `visits_total` | Visits |
| `activity_specialty_percentile` | Activity vs specialty peers (mean of visits and RVU percentiles; 100 = highest) |
| `in_system_provider` | In-system (PDC facility CCN) |
| `active_provider` | Active in the frozen window |
| `npi` | NPI (keep, can be a secondary column) |

`total` is the full match count (can be hundreds of thousands). Show it as “showing 1–50 of N”. Do not try to render N DOM rows.

Response also includes `state` and `mart_db` so the UI can confirm it hit the right market.

On row select, close the modal and `GET /v1/providers/{npi}?state=`.

## Group-practice dump

Same picker pattern, different grain: one row per **primary billing organization** (`primary_organization_id` on the Type 1 spine — the billing NPI from `physician_primary_affiliation`). Groups with a null id are omitted.

Page it the same way (`limit` default 50, max 500, `offset` + `total`). Default `min_visits=1` on the **sum** of member visits. Sort is server-side: `visits_total` desc, then `provider_count`, then name.

**This is not distinct encounters for the org.** `visits_total`, `panel_size`, and `wrvu_total` are sums across member NPIs. Two clinicians in the same group who billed the same encounter can both contribute. Show a caption: visits are summed across providers, not de-duplicated encounters. `activity_percentile` / `visits_percentile` are among **all** groups, not the current search filter.

Optional filters: `organization` (contains on group name), `parent` (contains on parent system name), `organization_id` (exact), `active` (only roll up NPIs active in the window), `in_system` (group has **at least one** facility-affiliated NPI), `min_visits` / `max_visits` (on the sum), `min_providers`.

| JSON | Table column |
| --- | --- |
| `organization_name` | Group |
| `parent_name` | Parent system |
| `provider_count` | Type 1 NPIs |
| `active_provider_count` | Active in window |
| `in_system_provider_count` | In-system members |
| `visits_total` | Sum of member visits |
| `visits_per_provider` | Sum visits / Type 1 count |
| `visits_percentile` | Percentile of summed visits among all groups (name filters do not change this) |
| `activity_percentile` | Percentile of visits per attributed NPI among all groups. This is the group activity score. Large orgs do not auto-win. |
| `panel_size` | Sum of member panel |
| `wrvu_total` | Sum of member RVU (label **RVU**) |
| `organization_id` | Billing NPI / group key (secondary column) |

Response includes `visits_are_summed_across_npis: true`. On group row select, `GET /v1/group-practices/{organization_id}?state=` for the **same five-tab profile as a provider**, with numbers and lists rolled up from member Type 1s. Dump members with `GET /v1/providers?state=&organization_id={id}&min_visits=1` (exact id, not a name contains). Then a member click is still `GET /v1/providers/{npi}`. Dump list rows stay slim (no nested arrays).

**Same caption as visits:** sites, referrals, hospital affiliations, panel, payers, top dx/px, and RVU on the group profile are summed across members and can double-count an encounter two clinicians billed. Top dx/px and their percents are counted from `pd_stg_visit` for members (share of the group’s `visits_total`). Mix percents are visit-weighted (panel percents are panel-weighted). The three dx/px shares will not add to 100%.

Do not treat `parent_name` / `hospital_affiliations` as CMS `in_system_provider`. That flag is still PDC facility CCN (`in_system_provider` on the group is true if **any** member has a CCN).

## Product locks

- Claims live in `{st}`, lookups in `{stal}`. The API reads **`{st}_pd` only**.
- Universe: Type 1 NPIs. Visit = distinct `encounter_id`.
- Frozen window: **Aug 2023 – Jul 2024** (`202308`–`202407`) until a `phase6 --slide`. Banner copy from `/v1/mart`: **as of {window_end month}**.
- Payers 1/2/3/4/5 as locked. Dummy NPIs 0 and 4 are dropped.
- `in_system_provider` is a **CMS Provider Data Catalog facility affiliation** (hospital CCN), not a Vue roster.
- Blank phones are OK. Do not invent a phone.
- Top 3 lists stay top 3 (diagnoses, procedures, payers, referrals in and out). Practice **sites** stay top 5 addresses. Hospital **affiliations** stay top 5 **distinct systems** (not five campuses of the same system).
- Weekend / after-hours is **UI-only**: `visits_percent_saturday` and `visits_percent_sunday` are already on the provider and each practice.

## RVU

Label it **RVU** in the UI. Do not say “wRVU”. Numbers are total-RVU scale, not CMS physician work RVU. Cardiology mean vs median is badly skewed (mean ~1317, median ~8.73). Show **median / p25 / p75 / percentile** (`wrvu_state_specialty_median`, `wrvu_state_specialty_p25`, `wrvu_state_specialty_p75`, `wrvu_specialty_percentile`). Do not lead with `wrvu_average` or `wrvu_state_specialty_average`. `wrvu_total` can sit as a supporting number next to visits.

## Activity vs specialty peers

Not quality, not MIPS. Peer group is `primary_specialty_code`.

| JSON | Meaning |
| --- | --- |
| `visits_specialty_percentile` | Visit volume vs other Type 1s in the same specialty (`visits_total > 0`). 100 = highest. |
| `wrvu_specialty_percentile` | RVU vs specialty peers with RVU > 0. Already existed. |
| `activity_specialty_percentile` | Mean of the two when both exist; otherwise the one that exists. |

Null if the NPI has no specialty or no visits/RVU. Schott-scale OTP volume will sit at the top of that NP’s specialty — that is volume, not a quality grade. After a code drop, ops must run `phase5` (no `--slide`) so the new columns fill; GET will show null until then.

## Profile layout (`GET /v1/providers/{npi}`)

Same payload as `python -m provider_directory.cli get --state AZ {npi}`.

**Sticky chrome (not a tab)** — banner from `/v1/mart` (“as of {window_end}”). Header: name, credential, specialty, estimated age / school if present, in-system badge, primary org + parent, NPI.

**Five tabs**, not eight sections and not one long scroll. Default tab is Overview.

| Tab | What’s on it |
| --- | --- |
| **Overview** | Volume **numbers**: visits, panel size, RVU total, specialty median / p25 / p75, **visits percentile**, **activity vs specialty peers** (`activity_specialty_percentile`). **Bars** for POS mix and Mon–Sun (including Sat/Sun). Ranked **lists** for top 3 dx and top 3 px (`visits_top_diagnosis_*_name` + `visits_top_diagnosis_*_percent`, same for procedures). Shares are of `visits_total` and will not add to 100%. **Group practices** (`group_practices[]`): primary first, then every other in-window billing org (`organization_name`; `billing_type` is `P` professional / `I` institutional). **Hospital affiliations** (`hospital_affiliations[]`): top-in **distinct** health systems (`hospital_system_name`), optional campus `facility_name`, visit share. Hide either list if empty. New vs established only if E/M counts exist. |
| **Sites** | Top 5 **addresses** as a **list/table**: name, city, work type, visit share, RVU share, phone if present, weekend % on the row. **No map.** Do not use lat/long in v1. Blank phone = blank cell. Do not put health systems here — those are Overview affiliations. |
| **Panel** | **Bars** for age bands and sex. **Bars** for payer mix (third-party / Medicaid / MA / FFS). Top 3 commercial parent **names** + percents. Hide a 0% extra payer. |
| **Referrals** | Two **lists**: in and out, top 3 each (peer name, specialty, patient count). No network graph. |
| **CMS** | Group size, telehealth offered, secondary specialties, MIPS, Open Payments (non-null kinds only; never `$0` for a missing kind), `utilization[]`. |

**Hide the CMS tab** when group size, telehealth, secondary specialties, MIPS, Open Payments, and `utilization[]` are all null/empty. Sean Smith still has group size / telehealth / MIPS, so the tab stays. Many NPs will have a thin CMS tab (Open Payments only, or nothing).

Hide any other block inside a tab when every field in it is null. Hide a POS bucket at 0% if the named buckets already tell the story.

## Group profile layout (`GET /v1/group-practices/{organization_id}`)

Reuse the **same five tabs and the same JSON keys** as the provider profile. Do not invent a second layout. The payload is `GroupPracticeProfile`: dump identity fields plus the provider metric/nested fields, aggregated.

**Sticky chrome** — same banner. Header: `organization_name`, `parent_name`, modal specialty (`primary_specialty_description` = members’ highest-visit specialty), `provider_count` Type 1s, in-system badge (`in_system_provider`), `organization_id` (billing NPI). No person name, age, school, credential, or gender.

| Tab | Same as provider, except |
| --- | --- |
| **Overview** | `visits_total`, `panel_size`, `wrvu_total`, **`activity_percentile`** / **`visits_percentile`** (among groups, not specialty peers — do not look for `activity_specialty_percentile`). POS and Mon–Sun bars. Top 3 dx/px names **and percents** (`visits_top_diagnosis_1_percent` …). Hospital affiliations. Hide `group_practices[]` (this page is the group). Caption: summed across providers. Drop the “re-ranked from members’ stored top 3” note; ranks and shares now come from member visits. |
| **Sites** | `practices[]` top 5 street+ZIP clusters, visits/RVU summed across members at that cluster. **No map.** |
| **Panel** | Age / sex / payer mix from weighted member percents. Top 3 commercial parents. |
| **Referrals** | Top 3 in and out; `patient_count` is summed. |
| **CMS** | `group_size` is max CMS `num_org_mem` on members (not `provider_count`). `telehealth_offered` if any member offers it. Open Payments are sums. Hide MIPS / `utilization[]` / secondary specialties (not group scores). |

`visits_are_summed_across_npis` is true on this payload. Keep the summed-across-providers caption on every tab that shows volume.

### Numbers vs bars

- **Numbers** for counts and scores: visits, panel size, RVU total, percentile, MIPS, Open Payments dollars, referral patient counts. Do not draw a bar for `visits_total` — Schott (~156k) vs Smith (6) would be a useless axis.
- **Bars** for mixes that sum toward 100%: POS, weekday, panel age, panel sex, payer mix. A simple horizontal stacked or small-multiples bar is enough. No chart library required if CSS bars are easier.
- **Lists** for ranked names: dx, px, group practices, hospital affiliations, sites, referrals. Dx/px send `visits_top_diagnosis_*_percent` / `visits_top_procedure_*_percent` (share of visits, not a 100% bar). Affiliations send `visit_share_pct`.

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
- Map view / using `latitude` / `longitude` (sites are a list)
- Geocoding beyond lat/long already on `practices`
- Loading the entire spine in one HTTP call

## New data (ops only, not the UI)

When the warehouse or CMS publishes, run CLI/`scripts/*.ps1` — never phase buttons.

- Claims month: `python -m provider_directory.cli sync --state AZ` (upserts new Type 1 NPIs from `{st}.physician`, then no-op unless `slide_available`; then slide + E/M/POS extras)
- Roster only: `sync --state AZ --spine` (insert missing NPIs, refresh name/specialty, overlay CMS if any were inserted). Never truncates.
- CMS identity: `sync --state AZ --cms`
- Open Payments: `sync --state AZ --open-payments`
- MIPS/utilization: `sync --state AZ --mips --utilization`

`sync --dry-run` prints the plan. Never `phase1`.

## After code drops

Copy `provider_directory/` into `C:\Users\jluna\Documents\Analysis Scripts`. Run **`phase4 --state AZ`** (fills `group_practices[]` / `hospital_affiliations[]`; do not `phase1`). Then restart NSSM. Until phase4, GET still works; those lists are empty.

```
python -m provider_directory.cli phase4 --state AZ
python -m provider_directory.cli get --state AZ 1952863797
python -m provider_directory.cli get --state AZ --min-visits 1 --limit 5
python -m provider_directory.cli groups --state AZ --min-visits 1 --limit 5
```

`--state` selects `{st}` / `{st}al` / `{st}_pd`. CMS national files in `data/cms` are shared. Never `phase1` from the UI.
