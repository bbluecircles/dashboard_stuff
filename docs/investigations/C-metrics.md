# C — Metrics rework: payer classes 1–7, top primary procedures, top 5, RVU average, visit dates

Repo: `/home/user/dashboard_stuff/provider_directory`. No DB access; facts are from code, with queries to confirm. Nothing was edited.

## 1. Payer classes 1–7

**Today.** `settings.py:100-105` locks `PAYOR_MEDICARE_FFS=1, PAYOR_MEDICAID=2, PAYOR_COMMERCIAL=3, PAYOR_HMO_MA=4, PAYOR_OTHER=5, PAYOR_MIX_CODES=(1,2,3,4)`. `analytics.rebuild_analytics` stages **every** class the lookup returns (`payor_claims_sql` only filters `py.is_payor_code IS NOT NULL`; `payor_sql` has no class filter), so codes 6 and 7 are probably already in `pd_stg_npi_payor` and simply ignored by `mix_sql`:

```sql
SUM(CASE WHEN is_payor_code = {PAYOR_HMO_MA} THEN claim_count ELSE 0 END) AS ma,
SUM(CASE WHEN is_payor_code IN ({mix}) THEN claim_count ELSE 0 END) AS denom
...
p.visits_percent_medicare_advantage = ROUND(100.0 * m.ma / NULLIF(m.denom, 0), 2),
```

Code 4 is named `HMO_MA` in settings but labeled **Medicare Advantage** in `field_help.py:320` and the UI spec (Panel tab, line 157). The business now says there are two new Medicare classes, "Medicare Adv" and "Medicare Other". If 6 = Medicare Advantage then 4 is probably a commercial/HMO class and the current column is mislabeled. **Do not relabel until the mapping query below has been run.**

**Confirm first (az_cms):**

```sql
-- class names and how many payer codes / parents sit in each
SELECT py.is_payor_code, py.is_payor_name, COUNT(*) AS payor_codes,
FROM az_cms.payor py GROUP BY 1, 2 ORDER BY 1;
-- if is_payor_name does not exist:
SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'az_cms' AND TABLE_NAME = 'payor' ORDER BY ORDINAL_POSITION;
-- claim volume per class in the window (summary table; native claims only on the blend)
SELECT d.is_payor_code, SUM(d.claim_count) AS claims, COUNT(DISTINCT d.rendering_physician_code) AS npis
FROM az_cms.dash_physician_payor_all d WHERE d.period_code BETWEEN 202308 AND 202407 GROUP BY 1 ORDER BY 1;
-- one month of pat_dt, encounter grain, same join Phase 4 uses
SELECT py.is_payor_code, COUNT(*) AS lines, COUNT(DISTINCT t.encounter_id) AS encounters
FROM az_cms.pat_dt t JOIN az_cms.payor py
  ON py.payor_code = COALESCE(NULLIF(TRIM(t.encounter_payor_code), ''), t.payor_code) COLLATE utf8mb4_unicode_520_ci
WHERE t.period_code = 202407 GROUP BY 1 ORDER BY 1;
-- what is already staged in the mart
SELECT is_payor_code, SUM(claim_count) AS claims, COUNT(DISTINCT npi) AS npis FROM az_pd.pd_stg_npi_payor GROUP BY 1 ORDER BY 1;
```

**Design.** Replace the five scalar constants with one table in `settings.py`:

```python
PAYOR_CLASSES = {  # code: (column suffix, UI label) — confirm against az_cms.payor before merging
    1: ("medicare_traditional", "Medicare FFS"), 2: ("medicaid", "Medicaid"), 3: ("third_party", "Commercial"),
    4: ("hmo", "HMO"),              # today's visits_percent_medicare_advantage; rename only if code 4 is NOT MA
    6: ("medicare_advantage", "Medicare Advantage"), 7: ("medicare_other", "Medicare Other"),
}
PAYOR_OTHER = 5
PAYOR_MIX_CODES = tuple(PAYOR_CLASSES)        # denominator = every mapped class except 5 Other
PAYOR_COMMERCIAL = 3                           # top payers stay class 3 unless 4 proves commercial
```

Denominator: all mapped classes (1,2,3,4,6,7); 5 Other and unmapped codes stay out, as today, so the six percents sum to 100. Top commercial payers stay class 3; if 4 proves to be commercial HMO, decide whether `top_sql`/`fetch_group_top_payers` should rank `IN (3,4)`.

New `pd_provider` columns: `visits_percent_medicare_other DECIMAL(6,2)` and, if 4 is not MA, `visits_percent_hmo`; `visits_percent_medicare_advantage` stays bound to whichever code is MA (never reuse a column with a new meaning). `migrate_phase4_columns` adds columns `IF NOT EXISTS`, so no `phase1`.

Every place that changes:

| File | Change |
| --- | --- |
| `settings.py:100-105` | table above |
| `analytics.py` reset `UPDATE` (~190-213), `mix_sql` (~394-416) | generate `SUM(CASE ...)` and `SET` lines from `PAYOR_CLASSES`; module docstring "Code 5 Other is excluded from the four percents" |
| `transforms.payer_mix_percents` (664-681) | same loop; its docstring |
| `schema.py` `PD_PROVIDER_PHASE4_COLUMNS` (137-140) and inline DDL (341-344) | new columns |
| `models.py` `ProviderSpine` 168-171, `GroupPracticeProfile` 335-338 | new fields |
| `group_profile.py` `VISIT_WEIGHTED` (25-45) | add new columns so group percents are visit-weighted |
| `field_help.py` `_PAYER_DEV` (218-223: "classes 1 to 4", "Class 5 Other"), entries 318-321, `CAVEATS` 526-529, `TABLE_REGISTRY["claims.payor"]` "payer class 1 to 5" | entries for new fields (the catalog test fails otherwise), relabel 4 if needed |
| `lookup.py` | nothing (`SELECT *` + pydantic) |
| `tests/test_analytics.py::test_payer_mix_excludes_other_from_denominator`, `test_phase4_sql_stays_on_mart_and_dash` ("is_payor_code = 5"), `test_schema_includes_phase4` | update; add a 6/7 case |
| `docs/ui-agent-spec.md:123` "Payers 1/2/3/4/5 as locked", 157, 175 Panel tab | six bars; `docs/fixtures/field-help.AZ_CMS*.json` regenerate |
| README Phase 4 paragraph | wording |

Rerun: Phase 4. `rebuild_analytics` drops Phase 4 staging and on AZ_CMS rescans 12 months of `pat_dt` for payers. Since `pd_stg_npi_payor` already holds the classes, add `phase4 --payers-only` (pipeline/cli; skip `drop_phase4_staging`, run only the reset of payer columns + `mix_sql` + `top_sql` + `rebuild_group_summary`): minutes instead of hours.

## 2. Top Primary Procedures (work RVU > 1)

`activity._rebuild_activity_from_staging` `top_px_sql` (activity.py:224-244) ranks every `pd_stg_visit.px`, then LEFT JOINs `azal.procd` only for the name:

```sql
ROW_NUMBER() OVER (PARTITION BY v.rendering_npi ORDER BY COUNT(*) DESC, v.px) AS rk
... GROUP BY v.rendering_npi, v.px ) ranked
LEFT JOIN {lookup}.procd pr ON pr.procd_code = ranked.code COLLATE {MART_COLLATION}
WHERE ranked.rk <= 3
```

**Change:** join `procd` *inside* the ranked subquery (INNER) and filter before ranking, so ranks 1..N are the top eligible codes:

```sql
FROM {mart}.pd_stg_visit v
INNER JOIN {mart}.pd_provider p ON p.npi = v.rendering_npi
INNER JOIN {lookup}.procd pr ON pr.procd_code = TRIM(v.px) COLLATE {MART_COLLATION}
WHERE v.px IS NOT NULL AND v.px <> ''
  AND {is_hcpcs_sql("v.px")}
  AND {strict_work_rvu_sql("pr")} > 1
```

**Raw `WORK_RVU` or `work_rvu_sql`?** Neither as-is. Raw `WORK_RVU > 1` drops real E/M codes whose `azal.procd.WORK_RVU` is corrupted (transforms.py:565-571 documents 93306/99204 summing to 0.01); the full `work_rvu_sql` admits codes through the `nf_total_rvu >= 0.5` fallback, which includes practice expense and is not a work RVU. Use a new `strict_work_rvu_sql` = steps 1–3 only (`WORK_RVU >= 0.05`, else non-facility total − PE − MP, else facility equivalent), no `nf_total_rvu`, no `WORK_RVU > 0`, excluding Category II codes (`pr.procd_code NOT REGEXP '^[0-9]{4}F$'`). This is the same expression section 4(b) proposes for pricing, so "top primary procedures" and "priced procedures" share one definition. Import it from `analytics` (activity already imports nothing from analytics; move the RVU SQL helpers to `transforms`/a new `rvu.py` to avoid a cycle — `analytics` imports `activity`).

Percent: keep the denominator `visits_total` in `lookup.fetch_top_code_percents` (line 302) and `group_profile.fetch_group_top_codes`; label stays "Share of visits" (shares never summed to 100). A share of eligible visits would need a new stored count.

Labels: `field_help.py` `_TOP_PX_DEV` (236-239) add the filter; entries 296-304 label "Top primary procedure N", help "...among procedures with a physician work RVU above 1"; `TABLE_REGISTRY["pd_stg_top_px"]`; new caveat "Procedures with work RVU of 1 or less are not ranked." UI spec Overview row (155): "Top Primary Procedures". `tests/test_activity.py::test_phase2_sql_uses_frozen_window_and_mart_only` add `assert "WORK_RVU" in source`.

Check before and after:

```sql
-- how many current top-3 entries would be dropped under each rule
SELECT SUM(pr.WORK_RVU > 1) AS raw_pass,
       SUM(COALESCE(CASE WHEN pr.WORK_RVU >= 0.05 THEN pr.WORK_RVU END,
                    CASE WHEN pr.NON_FACILITY_TOTAL - pr.NON_FAC_PE_RVU - pr.MP_RVU >= 0.05 THEN pr.NON_FACILITY_TOTAL - pr.NON_FAC_PE_RVU - pr.MP_RVU END,
                    CASE WHEN pr.FACILITY_TOTAL - pr.FACILITY_PE_RVU - pr.MP_RVU >= 0.05 THEN pr.FACILITY_TOTAL - pr.FACILITY_PE_RVU - pr.MP_RVU END) > 1) AS strict_pass,
       SUM(pr.procd_code IS NULL) AS no_procd_row, COUNT(*) AS rows_now
FROM az_pd.pd_stg_top_px t LEFT JOIN azal.procd pr ON pr.procd_code = t.code COLLATE utf8mb4_unicode_520_ci;
-- then list t.code, pr.WORK_RVU, reconstructed, SUM(visit_count) where the two rules disagree, ORDER BY visits DESC
```

**Rerun cost.** `phase2` (`rebuild_activity`) calls `drop_staging_tables(conn, mart_db)` with no table list, which drops **all** Phase 2–5 staging, then rescans `pat_dt` (hours) and forces phases 3–5. The cheap path exists but is only reachable via `phase6 --slide`: `slide_activity` does `drop_phase2_derived` + `create_schema` + `_rebuild_activity_from_staging` from the existing `pd_stg_window_claim` (`activity.py:400-470`); called with `drop_periods=[]`, `add_periods=[]` it is exactly "derived only" (it checks `pd_stg_window_claim` has rows first). Cheaper still, since `pd_stg_visit` would be rebuilt identically: add `phase2 --top-codes-only` that TRUNCATEs only `pd_stg_top_dx`/`pd_stg_top_px`, reruns `top_dx_sql`/`top_px_sql` per bucket, and overlays only the `visits_top_*` columns plus `rebuild_group_summary`. That leaves `pd_stg_visit`, Phase 3/4/5 staging and their cached tables untouched; expect minutes. Wire it through `pipeline.run_phase2` and `cli.py` (`phase2` parser, line ~394).

## 3. Top 5 for diagnoses, procedures, payers, referrals

Add `TOP_N = 5` to `settings.py` and set `MAX_REFERRAL_PEERS = TOP_N`. Thread it:

| Area | Where | Today |
| --- | --- | --- |
| Schema | `schema.py` `PD_PROVIDER_PHASE2_COLUMNS` 106-117 and DDL 316-327; `PD_PROVIDER_PHASE4_COLUMNS` 141-146 and DDL 345-350 | 3 × (code, name) for dx/px; 3 × (name, percent) payers. Add `visits_top_diagnosis_4/_4_name/_5/_5_name`, same for procedure, `top_payer_name_4/percent_4/5`. Generate the tuples with a loop; `migrate_phase2/4_columns` add them IF NOT EXISTS, so no `phase1`. `pd_stg_top_*` PK `(npi, rk)` needs no change |
| Staging rank | `activity.py:221,243` `WHERE ranked.rk <= 3`; `analytics.py:448` `WHERE rk <= 3`; `complete._rank_referrals` `rk <= {max_peers}` (already parameterized) | `<= {TOP_N}` |
| Overlay pivots | `activity.py` `reset_activity_columns_sql` null list and `overlay_sql` `MAX(CASE WHEN rk = N ...)` / `SET`; `analytics.py` reset `UPDATE` (203-208) and `top_sql` n1..n3/p1..p3 + `SET` (453-458) | generate from `range(1, TOP_N+1)` |
| Lookup | `lookup.fetch_top_code_percents:324` `rk > 3` | `rk > TOP_N` |
| Group profile | `group_profile.py:239` `LIMIT 3`, `:265` `rows[:3]`, docstring "Top 3"; `fetch_group_referrals` default `MAX_REFERRAL_PEERS` (ok) | `LIMIT %s` with TOP_N |
| Pure helpers | `transforms.top_n_codes` (351) and `top_commercial_payers` (687) `n=3` | default `TOP_N` |
| Models | `models.py` `ProviderSpine` 137-154, 172-177; `GroupPracticeProfile` 306-323, 339-344 | add 4/5 fields (code, name, percent; payer name, percent). `ProviderDumpRow` unchanged |
| Field help | `field_help.py` entries 287-304 and 322-327 (generate in a loop), `_group_scope` hardcoded percent keys 414-415, `_TOP_DX_DEV`/`_TOP_PX_DEV` "top 3", `_TOP_PCT_DEV` and `_caveats_for` 603/605 "the three shares"/"stored top 3", `CAVEATS` top_payer_percent 1-3, `REFERRAL["peer_rank"]` "Top 3", `PROVIDER["referrals"]`, `TABLE_REGISTRY` pd_stg_top_dx/px/pd_provider_referral descriptions | `test_every_api_field_has_help_in_both_tiers` fails until every new model field has an entry |
| Tests | `test_activity::test_schema_includes_phase2_columns_and_staging` (`visits_top_procedure_3`), `test_analytics::test_top_commercial_is_share_of_commercial_only`, `test_cli_and_models`, `test_group_profile`, `test_field_help` | add `_5` assertions, 5-peer referral case |
| Fixtures/docs | `docs/fixtures/field-help.AZ_CMS.json`, `.dev.json`, `.dev.md`, root `field-help.dev.json` (regenerate with the README commands); `docs/ui-agent-spec.md:114,126,155,157,158,173,175,176`; README Phase 4/5 "Top 3" | |

Phases: Phase 2 top-codes-only (section 2), `phase4 --payers-only` (section 1), Phase 5 (default `reuse_cached=True` only redoes referral edges from the dash table; minutes). Sites and affiliations already use 5.

## 4. RVU average rework

**What the code carries.** `pd_stg_window_claim` (schema.py:481-500) has one procedure column, `encounter_work_procd_code`, copied by `_window_claim_insert_sql` (activity.py:80-100). `pd_stg_visit.px = MAX(encounter_work_procd_code)` per encounter. The `encounter_` prefix pattern in `pat_dt` denotes encounter-level attributes denormalized onto every line: Phase 4 already uses both `t.payor_code` and `t.encounter_payor_code`, Phase 2 both `rendering_physician_code` and `encounter_rendering_physician_code`. By that pattern a line-level `procd_code` very likely exists next to `encounter_work_procd_code`, and `analytics.py:9` says "Line-level procd_dt sums are a later refinement" (a warehouse `procd_dt` table; `tests/test_complete.py:66` guards against reading it). Neither is confirmed. Run:

```sql
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'az_cms' AND (COLUMN_NAME LIKE '%procd%' OR COLUMN_NAME LIKE '%cpt%' OR COLUMN_NAME LIKE '%hcpcs%' OR COLUMN_NAME LIKE '%modifier%' OR COLUMN_NAME LIKE '%unit%')
ORDER BY TABLE_NAME, ORDINAL_POSITION;
SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'az_cms' AND TABLE_NAME LIKE '%procd%';
-- how often an encounter's lines disagree on encounter_work_procd_code (mart copy, indexed, one month)
SELECT COUNT(*) AS encounters, SUM(lines > 1) AS multi_line, SUM(codes > 1) AS multi_code,
       ROUND(100.0 * SUM(codes > 1) / COUNT(*), 2) AS pct_multi_code, SUM(lines) AS lines
FROM (SELECT encounter_id, COUNT(*) AS lines, COUNT(DISTINCT encounter_work_procd_code) AS codes
      FROM az_pd.pd_stg_window_claim WHERE period_code = 202407 AND encounter_id IS NOT NULL GROUP BY encounter_id) x;
-- repeat on az_cms.pat_dt for period_code = 202407 with COUNT(DISTINCT <line column>) once its name is known
```

If `multi_code` is near zero, `encounter_work_procd_code` is one code per encounter and a true per-procedure average needs variant (a).

**Current rule and its faults.** `analytics.work_rvu_sql` / `transforms.physician_work_rvu`: `WORK_RVU >= 0.05` → NF total − PE − MP ≥ 0.05 → facility equivalent → `nf_total_rvu >= 0.5` (includes practice expense) → `WORK_RVU > 0` (admits 0.001 junk). Category II codes (`1234F`) with non-zero hand-edited values are priced like procedures. `wrvu_average = wrvu_total / wrvu_procedure_count` is per visit, not per procedure.

**Variant (a): line-level code exists.** New staging `pd_stg_visit_line (id, encounter_id, period_code, rendering_npi, procd_code, units)` loaded per period × `VISIT_BUCKETS` from `pat_dt` (same shape as the Phase 4 payer scan; one 12-month scan, no drop of other staging) or, cheaper long-term, add `procd_code` to `_window_claim_insert_sql`/`pd_stg_window_claim` (needs one full Phase 2 reload). `pd_stg_npi_wrvu` becomes `(npi, total_wrvu = SUM(line rvu), procedure_count = COUNT(priced lines), visit_count = COUNT(DISTINCT encounter_id))`; same for `pd_stg_site_wrvu` and `pd_stg_npi_wrvu_prior` (`complete._fill_prior_wrvu` already scans `pat_dt`; switch its inner select to line grain and drop the cache). Columns: keep `wrvu_total` (now a line sum) and `wrvu_procedure_count` (true priced procedures); **`wrvu_average` becomes the headline average RVU per procedure**; add `wrvu_visit_count`. Benchmarks: `_fill_specialty_stats` adds `avg_per_procedure`, `median_per_procedure`, `p25/p75_per_procedure` over `pd_provider.wrvu_average` (NPIs with `wrvu_procedure_count >= 20`, to keep one-visit providers out), new `pd_provider` columns `wrvu_state_specialty_average_per_procedure`, `_median_per_procedure`, `_p25_per_procedure`, `_p75_per_procedure`, and `wrvu_average_specialty_percentile`; `_overlay_benchmarks` ranks on `wrvu_average` for it. Group: `fetch_group_metrics` sums `wrvu_procedure_count`, `attach_group_profile` divides (already does). Labels: `wrvu_average` → "Average RVU per procedure", `wrvu_total` → "RVU sum (all procedure lines)", specialty columns → "Specialty average RVU per procedure"; drop the "one procedure per visit" caption in `docs/ui-changes-urgent.md`.

**Variant (b): one code per visit.** Keep the staging; replace `work_rvu_sql` with the strict expression (steps 1–3, floor 0.05 on every step, `NOT REGEXP '^[0-9]{4}F$'`, no `nf_total_rvu`, no `WORK_RVU > 0`) and report the fallback: `pd_stg_npi_wrvu` gains `work_rvu_count` (step 1) and `reconstructed_count` (steps 2–3); `pd_provider` gains `wrvu_reconstructed_pct`. `wrvu_average` stays `total / procedure_count` but is now honestly "Average RVU per primary procedure (one per visit)"; benchmarks per procedure as in (a). `transforms.physician_work_rvu` and `test_work_rvu_prefers_plausible_pfs_columns` (`physician_work_rvu(work_rvu=0.001, nf_total_rvu=4.6) == 4.6`, `work_rvu=0.03 == 0.03`) change accordingly. In both variants Phase 4 and Phase 5 (drop `pd_stg_npi_wrvu_prior`) rerun.

Verification queries (azal / az_pd):

```sql
-- Category II codes carrying values in the hand-edited lookup (000_common.procd must never be read)
SELECT procd_code, procd_name, WORK_RVU, nf_total_rvu FROM azal.procd WHERE procd_code REGEXP '^[0-9]{4}F$' AND (WORK_RVU > 0 OR nf_total_rvu > 0);
-- which rule step prices today's visits
SELECT CASE WHEN pr.WORK_RVU >= 0.05 THEN '1 work' WHEN pr.NON_FACILITY_TOTAL - pr.NON_FAC_PE_RVU - pr.MP_RVU >= 0.05 THEN '2 nf recon'
            WHEN pr.FACILITY_TOTAL - pr.FACILITY_PE_RVU - pr.MP_RVU >= 0.05 THEN '3 fac recon' WHEN pr.nf_total_rvu >= 0.5 THEN '4 nf_total' WHEN pr.WORK_RVU > 0 THEN '5 tiny work' ELSE 'none' END AS step,
       COUNT(*) AS visits, SUM(v.px REGEXP '^[0-9]{4}F$') AS f_codes
FROM az_pd.pd_stg_visit v JOIN azal.procd pr ON pr.procd_code = TRIM(v.px) COLLATE utf8mb4_unicode_520_ci
WHERE CHAR_LENGTH(TRIM(v.px)) = 5 AND MOD(ABS(v.encounter_id), 16) = 0 GROUP BY step;
```

## 5. pd_stg_visit_date source

`complete._fill_visit_dates` (complete.py:333-372) needs one `DATE` per `pd_stg_visit.encounter_id`: it takes `MIN(service_end_date)` over the encounter's `az_cms.pat_dt` lines, parsed from `YYYYMMDD` text (`service_date_sql`, `is_ymd_sql`), joined on `t.encounter_id = v.encounter_id`, one scan per period × 16 buckets, cached in `pd_stg_visit_date (encounter_id, service_end_date)`. Consumers (`_fill_npi_dow`, `_fill_site_dow`) only need `DAYOFWEEK`. The business did not name the replacement table; find candidates with:

```sql
SELECT c.TABLE_NAME, t.TABLE_ROWS, GROUP_CONCAT(c.COLUMN_NAME ORDER BY c.ORDINAL_POSITION) AS date_like_columns,
       MAX(c.COLUMN_NAME = 'encounter_id') AS has_encounter_id
FROM information_schema.COLUMNS c JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
WHERE c.TABLE_SCHEMA = 'az_cms' AND (c.COLUMN_NAME = 'encounter_id' OR c.COLUMN_NAME LIKE '%date%' OR c.COLUMN_NAME LIKE '%_dt' OR c.COLUMN_NAME LIKE 'dos%' OR c.COLUMN_NAME LIKE '%admit%' OR c.COLUMN_NAME LIKE '%discharge%' OR c.COLUMN_NAME LIKE '%service_start%' OR c.COLUMN_NAME LIKE '%service_end%')
GROUP BY c.TABLE_NAME, t.TABLE_ROWS HAVING has_encounter_id = 1 ORDER BY t.TABLE_ROWS DESC;
```

Then, for each candidate `<T>.<date_col>` (replace the placeholders; do not assume a format), compare coverage and agreement with the cached dates for one month:

```sql
SELECT COUNT(*) AS visits, SUM(d.encounter_id IS NOT NULL) AS dated_from_pat_dt, SUM(c.encounter_id IS NOT NULL) AS dated_from_candidate,
       SUM(c.d IS NOT NULL AND d.service_end_date IS NOT NULL AND c.d <> d.service_end_date) AS disagree,
       SUM(c.d IS NOT NULL AND DAYOFWEEK(c.d) <> DAYOFWEEK(d.service_end_date)) AS dow_disagree
FROM az_pd.pd_stg_visit v
LEFT JOIN az_pd.pd_stg_visit_date d ON d.encounter_id = v.encounter_id
LEFT JOIN (SELECT encounter_id, MIN(CAST(<date_col> AS DATE)) AS d FROM az_cms.<T> GROUP BY encounter_id) c ON c.encounter_id = v.encounter_id
WHERE v.period_code = 202407;
```

Once chosen: a settings tuple `VISIT_DATE_SOURCE = ("<T>", "encounter_id", "<date_col>", "ymd"|"date")` read by `_fill_visit_dates` (keep the `MIN()` + `LEAST()` accumulation), a `phase5 --rebuild-dates` flag that drops `pd_stg_visit_date`, and updates to `_DOW_DEV` (`field_help.py:224-227`), `TABLE_REGISTRY["pd_stg_visit_date"].built_from` and `tests/test_complete.py:65`. An encounter-grain table needs one query per bucket, not per period × bucket.

## 6. Implementation plan

Ordered so each rebuild happens once. Hours are engineering; rerun times are Galera wall-clock.

1. **Confirm facts (0.5 h + DBA time).** Run the section 1 payor queries, section 4 information_schema and multi-code queries, section 5 candidate query. Decisions: meaning of class 4; line-level procedure column present or not; visit-date table.
2. **Settings and shared SQL (3 h).** `TOP_N`, `PAYOR_CLASSES`, strict RVU expression in one module; `activity`/`analytics`/`complete`/`transforms` import it. Tests: `test_analytics`, `test_activity` source assertions.
3. **Schema + models + field help for top 5 and payer 6/7 (4 h).** `schema.py` tuples/DDL generated in loops, `models.py`, `field_help.py` loops, `group_profile.VISIT_WEIGHTED`, `lookup.fetch_top_code_percents`. Run `pytest`; regenerate the three fixtures and root `field-help.dev.json`.
4. **Top primary procedures (2 h).** `top_px_sql` filter, labels, caveat, `phase2 --top-codes-only` (cli/pipeline/activity), test for the flag. Rerun: `phase2 --top-codes-only` (minutes).
5. **Payer mix 1–7 and top 5 payers (2 h).** `mix_sql`/`top_sql`/reset generated from the tables; `phase4 --payers-only`. Rerun: `phase4 --payers-only` (minutes). Then `phase5` for 5 referral peers (reuses caches; tens of minutes).
6. **RVU rework (variant b: 6 h; variant a: 12 h).** (b) swap the expression, fallback counters, per-procedure benchmarks, labels; rerun `phase4` (payer scan again unless `--payers-only` is combined with a `--wrvu-only`; add both flags as one `--overlay` group) and `phase5` after dropping `pd_stg_npi_wrvu_prior` (prior-year `pat_dt` scan, hours). (a) adds the line staging loader (one 12-month `pat_dt` scan, Phase 2-like hours), new tables in `schema.py`/`TABLE_REGISTRY`, `extras.py` untouched (it reads `pd_stg_visit.px`).
7. **Visit date source (3 h after the table is known).** Settings tuple, `_fill_visit_dates`, cache drop flag, field help, test. Rerun: `phase5 --rebuild-dates` (one scan of the chosen table).
8. **Docs (2 h).** `docs/ui-agent-spec.md` (top 5, six payer bars, "Top Primary Procedures", RVU labels), `docs/ui-changes-urgent.md` RVU table, README phases 2/4/5, `settings.py` docstring, NSSM restart note.
9. **Verification (2 h).** Smoke NPIs `1952863797`, `1609236967` and one group via CLI `get`/`groups`; the section 2 and 4 count queries before/after; `GET /v1/field-help?dev=true` renders every new key.

Total: about 25 h (variant b) or 31 h (variant a), plus three rebuild windows: top-codes + payer overlays (minutes), the RVU Phase 4/5 rerun (hours), the visit-date rebuild (one scan). Not for the demo build; `docs/ui-changes-urgent.md` already defers these items.
