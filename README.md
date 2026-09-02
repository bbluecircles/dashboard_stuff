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

Same DB env as `db_snapshot.py`: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`. Optional: `PD_MART_DB` (default `az_pd`), `PD_CLAIMS_DB` (`az`), `PD_LOOKUP_DB` (`azal`).

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
