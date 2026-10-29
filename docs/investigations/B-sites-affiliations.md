# Investigation B: practice sites and affiliations

Repo: `/home/user/dashboard_stuff` (branch `claude/awesome-albattani-ceq65j`; `claude/pd-rework` is identical in `locations.py`/`affiliations.py`). No DB access; data claims are paired with queries. Paths are under `/home/user/dashboard_stuff/provider_directory/` unless noted.

## 1. Why sites are named "Unknown"

### The name expression (Phase 3)

`locations.py:107-123 practice_name_sql()` is a `COALESCE` evaluated once per `(npi, sl_code)` in `rebuild_locations()` (`npi_sl_sql`, lines 313-349) and stored in `pd_stg_npi_sl.name`; `practice_sql` (355-415) copies the busiest location's name into `pd_provider_practice.name`. Order:

1. `NULLIF(TRIM(sl.sl_hospital_system_name), '')`
2. `NULLIF(TRIM(fac.provider_facility_npi_hospital_system_name), '')`
3. `CASE WHEN sl.npi_type = '2' THEN NULLIF(TRIM(sl.sl_dba_name), '') END`
4. same for `sl.sl_name`
5. `fac.PROVIDER_FACILITY_NPI_dba_name`
6. `org_or_null_sql(sl.sl_common_name)`, `(sl.sl_dba_name)`, `(sl.sl_name)`
7. `street`, then `city`

**Root cause of the Mayo example.** Steps 1-2 have no `'UNKNOWN'` guard. The warehouse uses the literal `Unknown` as a sentinel (the same file knows this: `hospital_system_name_sql()` line 137 drops `'UNKNOWN', 'UNKNOWN GROUP PRACTICE'`, and `affiliations.py:66,360` do too). So a Type 2 `sl` row with `sl_dba_name = 'Mayo Clinic Scottsdale'` and `sl_hospital_system_name = 'Unknown'` is named `Unknown`, because the system name wins over the DBA. This matches "npi_type 2, name Unknown".

### Every path that yields "Unknown" or null

| Path | Where | Result |
| --- | --- | --- |
| `sl_hospital_system_name = 'Unknown'` | `practice_name_sql` step 1 | `Unknown` (beats DBA) |
| `provider_facility_npi_hospital_system_name = 'Unknown'` | step 2 | `Unknown` |
| Type 2 `sl_dba_name`/`sl_name = 'Unknown'` | steps 3-4, no guard | `Unknown` |
| `org_or_null_sql()` (lines 88-97) | returns the value unless blank or `LAST, FIRST` without an org hint; `Unknown` is neither | `Unknown` passes through |
| `hospital_facility_name_sql()` (143-154) | same candidates, no guard | `facility_name = 'Unknown'` in `pd_provider_hospital_affiliation` (insert at `affiliations.py:276-281` only nulls blank or equal-to-system) |
| All candidates blank, no street/city | step 7 | `NULL` name |
| Name is exactly `TYPE-2-ENTITY` | `REGEXP_REPLACE` then `TRIM` | empty string `''`, not NULL |
| Phase 4 polish `analytics.py:521-540` | only rows with `npi_type = '1'` whose name matches `is_person_practice_name_sql` (`^[^,]+,[[:space:]]*[^,]+$` and no `ORG_NAME_REGEXP` hint) become `street, city` | does **not** touch `Unknown`; also leaves a Type 2 `LAST, FIRST` DBA (steps 3-4 skip the person check) |

The Python twin `transforms.py:523-552 pick_practice_name()` does **not** reproduce the bug: `strip_entity_suffix()` -> `nonempty()` drops `UNKNOWN` via `_EMPTY` (line 22). Tests (`tests/test_locations.py:50-72`) only exercise the Python path, so the SQL divergence is untested.

Downstream display: `lookup.py:547` (`pr.name AS practice_name` for the picker), `group_profile.py:345` (`pick_top_sql(pr, "name")`) pass the value through unchanged.

### Column names: `dba_name` vs `sl_dba_name`

Code references on `az_cms.sl`: `sl_code, sl_name, sl_dba_name, sl_common_name, sl_hospital_system_name, npi_type, street, city, county_name, state_abbr, zip_code, latitude, longitude, pos_type_code, pos_type_name, im_specialty_rollup` (`locations.py:107-164, 322-347`; `analytics.py:111-141`; `extras.py:83-110`). MariaDB raises `Unknown column` for a missing column, so if Phase 3 completed against `az_cms` those names exist; but a populated `dba_name` alongside an empty `sl_dba_name` would not error. Confirm:

```sql
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'az_cms' AND TABLE_NAME = 'sl'
ORDER BY ORDINAL_POSITION;

-- fill rates of every name-ish column
SELECT COUNT(*) AS rows_,
       SUM(NULLIF(TRIM(sl_dba_name), '') IS NULL)          AS sl_dba_blank,
       SUM(NULLIF(TRIM(sl_name), '') IS NULL)              AS sl_name_blank,
       SUM(NULLIF(TRIM(sl_common_name), '') IS NULL)       AS common_blank,
       SUM(UPPER(TRIM(sl_hospital_system_name)) = 'UNKNOWN') AS system_unknown,
       SUM(NULLIF(TRIM(sl_hospital_system_name), '') IS NULL) AS system_blank
FROM az_cms.sl;
-- if the listing shows a bare dba_name, repeat with dba_name in place of sl_dba_name
```

Practice rows named Unknown/blank, by `npi_type` and by which `sl` name columns are blank:

```sql
SELECT pr.npi_type,
       UPPER(TRIM(IFNULL(pr.name, ''))) IN ('UNKNOWN', '')          AS name_unknown_or_blank,
       UPPER(TRIM(IFNULL(sl.sl_hospital_system_name, ''))) = 'UNKNOWN' AS sys_unknown,
       NULLIF(TRIM(sl.sl_hospital_system_name), '') IS NULL        AS sys_blank,
       NULLIF(TRIM(sl.sl_dba_name), '') IS NULL                    AS dba_blank,
       NULLIF(TRIM(sl.sl_name), '') IS NULL                        AS name_blank,
       NULLIF(TRIM(sl.sl_common_name), '') IS NULL                 AS common_blank,
       COUNT(*) AS practice_rows, COUNT(DISTINCT pr.npi) AS npis
FROM az_pd.pd_provider_practice pr
LEFT JOIN az_cms.sl sl ON sl.sl_code = pr.sl_code
WHERE pr.name IS NULL OR UPPER(TRIM(pr.name)) IN ('UNKNOWN', '')
GROUP BY 1,2,3,4,5,6,7
ORDER BY practice_rows DESC;

-- the Mayo example
SELECT sl_code, npi, npi_type, sl_name, sl_dba_name, sl_common_name,
       sl_hospital_system_name, pos_type_code, pos_type_name, street, zip_code
FROM az_cms.sl
WHERE UPPER(street) LIKE '13400 E SHEA%';
```

### Fix (code)

Replace `practice_name_sql()` with: `sl_dba_name` (or `dba_name`, per the column query) -> `sl_name` -> `sl_common_name` -> `street, city`, every candidate wrapped in a shared `clean_name_sql(expr)` that returns NULL for blank, `UNKNOWN`, `UNKNOWN GROUP PRACTICE`, `NA`, `N/A`, `NULL`, `NONE` (mirror `_EMPTY`), strips `TYPE-2-ENTITY`, and applies the person-name rule to *all* candidates (not only steps 6). No hospital-system or `provider_facility_npi` inputs. Keep the Phase 4 `street, city` polish as a safety net but extend it to any name that is blank/Unknown regardless of `npi_type`. Apply the same guard to `hospital_facility_name_sql` if that table survives (section 4).

## 2. Every use of `az.provider_facility_npi` and its replacement

| Use | Replacement |
| --- | --- |
| `locations.py:345-346` `LEFT JOIN {claims}.provider_facility_npi fac ON fac.PROVIDER_FACILITY_NPI_code = sl.sl_code` | delete the join |
| `practice_name_sql` lines 113, 116 (`provider_facility_npi_hospital_system_name`, `PROVIDER_FACILITY_NPI_dba_name`) | `sl.sl_dba_name` / `sl.sl_name` / `sl.sl_common_name` only |
| `hospital_system_name_sql` line 131 | function deleted (section 4) or `sl.sl_hospital_system_name` only |
| `hospital_facility_name_sql` line 149 | `sl.sl_dba_name`, `sl.sl_name`, `sl.sl_common_name` |
| `work_type_sql` line 160 | drop; use `analytics.work_type_case_sql` (section 5) |
| `affiliations.py:322-323` same join in `hospital_sql` | delete with the hospital build |
| `transforms.pick_practice_name()` params `facility_dba`, `facility_hospital_system` (527-552) | remove parameters; keep the Python twin in sync |
| `field_help.py:72` registry `claims.provider_facility_npi`; `:80`, `:82` `built_from`; `:468-469` dev text | remove entries; dev text becomes `az.sl.sl_dba_name, else sl_name, else sl_common_name` |
| `tests/test_locations.py:112` (`assert "provider_facility_npi" in source`), `tests/test_affiliations.py:30` | invert to `not in` |
| generated fixtures `docs/fixtures/field-help.AZ_CMS.*`, `field-help.dev.json` | regenerate with `cli field-help --out` |

## 3. Why Judd Cummings shows 25.6% instead of 100%

### Exact computation

`affiliations.py:267-334 hospital_sql`:

- Inner rows `x`: `pd_stg_npi_sl s INNER JOIN az_cms.sl LEFT JOIN provider_facility_npi`, with `hospital_system_name = hospital_system_name_sql()` (= `sl_hospital_system_name`, else facility system; blank/Unknown -> NULL).
- `WHERE x.hospital_system_name IS NOT NULL`, `GROUP BY x.npi, UPPER(x.hospital_system_name)`; `visits_at_system = SUM(x.visits)`.
- `pd_stg_npi_sl.visits` = `COUNT(*)` of `pd_stg_visit_site` per `(rendering_npi, sl_code)` (`locations.py:339-343`); `pd_stg_visit_site` holds one row per encounter with the **modal `sl_code` over its `pd_stg_window_claim` lines, excluding NULL and dummy 0/4** (lines 277-307); and `npi_sl_sql` drops sites with `state_abbr = 'XX'` or a PO-box street (347-348).
- `visit_share_pct = ROUND(100.0 * visits_at_system / NULLIF(p.visits_total, 0), 2)` (line 284). `pd_provider.visits_total` = `COUNT(*)` of **all** `pd_stg_visit` encounters for the NPI (`activity.py:262-266`), with or without a service location.
- Rows kept only if `affiliation_rank <= 5` and share `>= 2.0` (`settings.py:93-94`).
- `parent_sql` (342-366) adds a rank-1 row from `pd_provider.primary_organization_parent_name` with `visit_share_pct NULL` only when the NPI got no system row.

So 25.6% means: of all his encounters, 25.6% landed (by modal `sl_code`) on `sl` rows whose system string equals, case-insensitively, that one value. The other 74.4% are in `visits_total` but not under that string. Causes, each independently sufficient:

1. **Encounters with no `sl_code` or dummy 0/4** on every line: counted in `visits_total`, absent from `pd_stg_visit_site`.
2. **Sites with blank or `Unknown` `sl_hospital_system_name`** (and no facility fallback): `hospital_system_name_sql` returns NULL, the visits count toward no system. Hospital-owned clinics and Type 1 `sl` rows (the clinician's own NPI) are typically like this.
3. **Campus strings that differ** (`Banner Health` vs `Banner Desert Medical Center` vs `BANNER - UNIVERSITY MEDICAL CENTER PHOENIX`): grouping is `UPPER()` only, so each becomes its own "system"; those under 2% or past rank 5 vanish.
4. **Modal-site assignment**: an encounter with lines at the hospital `sl` and at a professional/office `sl` goes to whichever has more lines (ties -> lowest `sl_code`).
5. **Sites dropped by `npi_sl_sql`** (`state_abbr = 'XX'`, PO box) or missing from `az_cms.sl` (`INNER JOIN`).
6. The denominator is `visits_total`, never "visits with a known system".
7. The parent-fallback row is not his case (it has NULL share), but it is another way a profile shows a name without visit evidence.

### Diagnostic queries (`SET @npi := <NPI>`)

```sql
SET @npi := 1234567890;

-- a) denominator vs sited visits
SELECT p.visits_total,
       (SELECT COUNT(*) FROM az_pd.pd_stg_visit_site vs WHERE vs.rendering_npi = @npi) AS visits_with_site,
       (SELECT SUM(visits) FROM az_pd.pd_stg_npi_sl s WHERE s.npi = @npi)          AS visits_in_npi_sl
FROM az_pd.pd_provider p WHERE p.npi = @npi;

-- b) every site with visits, system string, POS, and whether it counted toward a system
SELECT s.sl_code, s.visits, s.cluster_key, s.name AS site_name, s.npi_type,
       sl.sl_hospital_system_name, sl.sl_dba_name, sl.sl_name,
       sl.pos_type_code, sl.pos_type_name, sl.im_specialty_rollup, sl.state_abbr,
       CASE WHEN NULLIF(TRIM(sl.sl_hospital_system_name), '') IS NULL THEN 'blank'
            WHEN UPPER(TRIM(sl.sl_hospital_system_name)) IN ('UNKNOWN','UNKNOWN GROUP PRACTICE') THEN 'unknown'
            ELSE 'counted' END AS system_status,
       ROUND(100.0 * s.visits / NULLIF(p.visits_total, 0), 2) AS share_of_total
FROM az_pd.pd_stg_npi_sl s
JOIN az_pd.pd_provider p ON p.npi = s.npi
LEFT JOIN az_cms.sl sl ON sl.sl_code = s.sl_code
WHERE s.npi = @npi
ORDER BY s.visits DESC;

-- c) what Phase 4 stored
SELECT * FROM az_pd.pd_provider_hospital_affiliation WHERE npi = @npi ORDER BY affiliation_rank;

-- d) encounters that never reached a site (no / dummy sl_code on every line)
SELECT COUNT(*) AS unsited_visits
FROM az_pd.pd_stg_visit v
LEFT JOIN az_pd.pd_stg_visit_site vs ON vs.encounter_id = v.encounter_id
WHERE v.rendering_npi = @npi AND vs.encounter_id IS NULL;

-- e) sites excluded by the XX / PO-box filter
SELECT c.sl_code, COUNT(DISTINCT c.encounter_id) AS encounters, sl.state_abbr, sl.street
FROM az_pd.pd_stg_visit v
JOIN az_pd.pd_stg_window_claim c ON c.encounter_id = v.encounter_id
LEFT JOIN az_cms.sl sl ON sl.sl_code = c.sl_code
WHERE v.rendering_npi = @npi
  AND (c.sl_code IS NULL OR c.sl_code IN (0,4) OR IFNULL(sl.state_abbr,'') = 'XX' OR sl.sl_code IS NULL)
GROUP BY c.sl_code, sl.state_abbr, sl.street;
```

## 4. Redefining "affiliation" from `master_ref.111_physician`

**Source facts.** One row per NPI; `affiliation_code`, `affiliation_name`, `affiliation_dba_name`; often blank; placeholder values `'888' + NPI` (code) and `'NAME-NPI'` (name) mark a self-affiliated / unaffiliated clinician. Because the grain is one row per NPI, **the profile can carry at most one affiliation**; there is no ranking, no visit share, and no list.

**Definition.** `affiliation` = the organization on the clinician's `master_ref.111_physician` row, when it is a real organization:

```
affiliation_name_display = COALESCE(clean(affiliation_dba_name), clean(affiliation_name))
where clean() nulls blank / 'UNKNOWN' and the placeholders:
  affiliation_code = CONCAT('888', npi)            -> none
  affiliation_name/dba REGEXP CONCAT('-', npi, '$') -> none   ('NAME-NPI')
```

Confirm the placeholder shapes on that server before coding:

```sql
SELECT SUM(affiliation_code = CONCAT('888', npi)) AS self_code,
       SUM(affiliation_name LIKE CONCAT('%-', npi)) AS self_name,
       SUM(NULLIF(TRIM(affiliation_name), '') IS NULL) AS name_blank,
       SUM(NULLIF(TRIM(affiliation_dba_name), '') IS NULL) AS dba_blank,
       COUNT(*) AS rows_, COUNT(DISTINCT npi) AS npis
FROM master_ref.`111_physician`;
```

**Where it lives.** New `pd_provider` columns (`schema.py` `PD_PROVIDER_*_COLUMNS` + a `migrate_*` ALTER): `affiliation_code VARCHAR(40)`, `affiliation_name VARCHAR(180)`, `affiliation_dba_name VARCHAR(180)`, `affiliation_source VARCHAR(16)` (`'master_ref'` or NULL). Because the source is another server, load it the way CMS files are loaded: a `sync --state AZ --affiliations` step (`sync.py`) that reads via a second DSN (`PD_MASTER_REF_DSN` in `settings.py`) or a dumped CSV into a mart copy `az_pd.ref_physician_affiliation (npi PK, affiliation_code, affiliation_name, affiliation_dba_name, loaded_at)`, then overlays `pd_provider` by NPI. Expose on `ProviderSpine`/`ProviderDumpRow` (`models.py`) and the group profile as a member rollup (`COUNT(DISTINCT npi)` per `affiliation_name`, top 5) if wanted.

**`pd_provider_hospital_affiliation`: drop it.** The UI already ignores `hospital_affiliations[]` (`docs/ui-changes-urgent.md`), the business distrusts the grouping, and keeping it costs a Phase 4 build plus an API payload. Concretely: remove the `hospital_sql`/`parent_sql` blocks from `affiliations.py` (keep the group-practice half), delete `hospital_system_name_sql`/`hospital_facility_name_sql`, remove `HospitalAffiliation`, `hospital_affiliations` fields and `fetch_hospital_affiliations`/`fetch_group_hospital_affiliations` (`lookup.py:188-285`, `group_profile.py:436,485`), remove the table from `schema.TABLES` with a `DROP TABLE IF EXISTS` migration, and delete `MAX_HOSPITAL_AFFILIATIONS`/`MIN_HOSPITAL_AFFILIATION_SHARE_PCT`. Query b) above reproduces the visit-by-system view from staging if ever needed.

**Field help** (`field_help.py`): remove the `hospital_affiliation` scope from `SCOPES` (line 34), `catalog()` (651) and `tests/test_field_help.py:26`; remove `provider.hospital_affiliations` (373) and `CAVEATS`/`DERIVED_FROM` rows (557-558, 583-584). Add to `PROVIDER` (and the group passthrough):

- `affiliation_name`: label **Affiliation**; help "The organization the clinician is affiliated with in the master reference file. One per clinician; blank when none is on file."; dev "master_ref.111_physician.affiliation_dba_name, else affiliation_name, on the clinician's row. Code '888'+NPI and name 'NAME-NPI' are self-affiliation placeholders and are shown as blank. Not derived from visits."
- `affiliation_code`: label **Affiliation code**; help "Reference identifier of the affiliated organization."
- `affiliation_source`: label **Affiliation source**; help "master_ref when the reference file had a row; blank otherwise."
- Register `"ref.111_physician"` in `TABLE_REGISTRY` (kind `warehouse`, description "Physician master reference: one row per NPI with its affiliated organization"), and `ref_physician_affiliation` as a mart table built from it.

`practice.name` dev text becomes "az.sl.sl_dba_name, else sl_name, else sl_common_name; blank, Unknown and LAST, FIRST values fall through to street and city." The existing test `test_client_tier_never_says_hospital_system_or_bare_rvu` keeps the wording honest.

## 5. Work type

Two writers today:

- Phase 3 `locations.py:157-164 work_type_sql()`: if `COALESCE(sl.sl_hospital_system_name, fac...)` is non-blank -> `im_specialty_rollup` or `'Hospital'`; else `pos_type_name` or `im_specialty_rollup`. Stored in `pd_stg_npi_sl.work_type` and copied into `pd_provider_practice`.
- Phase 4 `analytics.py:509-519` overwrites **every** `pd_provider_practice` row joined to `az_cms.sl` with `work_type_case_sql()` (111-141): `pos_type_code` via `POS_WORK_TYPE`, else rollup keywords, else `pos_type_name`, else `im_specialty_rollup`. `extras.pos_mix_bucket_sql` reuses it.

Changing the site-name source does not affect Phase 4's rule, so **work type still holds**. Two defects to fix while there: (a) Phase 3's rule depends on the hospital-system string, so `Unknown` system makes `work_type = 'Hospital'` until Phase 4 runs; replace `work_type_sql` with `work_type_case_sql` so Phase 3 and 4 agree and the facility join goes away. (b) **`Unknown` can leak**: the last two `COALESCE` arms (`pos_type_name`, `im_specialty_rollup`) have no sentinel guard, so a warehouse `pos_type_name = 'Unknown'` becomes `work_type = 'Unknown'`; the Python `polish_work_type` (via `nonempty`) would not. Guard both arms with the same `clean_name_sql`. Confirm:

```sql
SELECT work_type, COUNT(*) FROM az_pd.pd_provider_practice GROUP BY work_type ORDER BY 2 DESC;
SELECT pos_type_code, pos_type_name, COUNT(*) FROM az_cms.sl GROUP BY 1,2 ORDER BY 3 DESC LIMIT 40;
SELECT COUNT(*) FROM az_cms.sl WHERE UPPER(TRIM(pos_type_name)) = 'UNKNOWN' OR UPPER(TRIM(im_specialty_rollup)) = 'UNKNOWN';
```

## 6. Implementation plan

Prereq (0.5 h): run the column/fill-rate queries in section 1 and the placeholder query in section 4; decide `sl_dba_name` vs `dba_name`.

1. **Site naming, Phase 3** (3 h). `locations.py`: add `clean_name_sql()`; rewrite `practice_name_sql()` (DBA -> name -> common -> street, city, all guarded, person rule on all); drop the `provider_facility_npi` join and `hospital_system_name_sql`/`hospital_facility_name_sql`; replace `work_type_sql` with `analytics.work_type_case_sql` (move it to `transforms`/a shared module to avoid the `analytics -> affiliations -> locations` import cycle). `transforms.pick_practice_name()`: same order, drop facility params. Tests: `tests/test_locations.py` (assert `UNKNOWN` guard present, `provider_facility_npi` absent, Type 2 `LAST, FIRST` falls to street), `tests/test_transforms.py`.
2. **Phase 4 polish and work type** (1.5 h). `analytics.py:521-540`: extend the rewrite to blank/Unknown names of any `npi_type`; guard the `work_type_case_sql` tail arms. Tests in `tests/test_analytics.py`.
3. **Remove hospital affiliations** (3 h). `affiliations.py` (delete hospital + parent fallback, keep group practices), `schema.py` (table + migration drop, settings constants), `models.py`, `lookup.py`, `group_profile.py`, `api.py` docs, `cli.py` help strings, `README.md`, `docs/ui-agent-spec.md`. Tests: `tests/test_affiliations.py`, `test_api.py`, `test_group_profile.py`, `test_cli_and_models.py`.
4. **master_ref affiliation** (4 h). `settings.py` DSN/CSV path; `schema.py` `ref_physician_affiliation` + `pd_provider` columns + migration; `sync.py` `--affiliations` loader and overlay with placeholder cleaning; `models.py` fields; `group_profile.py` member rollup (optional). Tests: `tests/test_sync.py`, `test_transforms.py` (placeholder detection as a pure function).
5. **Field help** (1.5 h). `field_help.py` registry, scopes, entries, caveats; regenerate `docs/fixtures/*` and `field-help.dev.json` with `cli field-help --out`; `tests/test_field_help.py`.
6. **Rebuild and verify** (2 h wall, mostly waiting). Order: `phase3 --state AZ_CMS` (recreates `pd_stg_npi_sl`, `pd_provider_practice`), then `phase4 --state AZ_CMS` (work_type polish, name polish, group lists; no hospital build), then `sync --state AZ_CMS --affiliations`, then `extras` (POS mix reads `work_type_case_sql`), then restart NSSM. Verify with the section 1 count query (expect zero `Unknown`), `get --state AZ_CMS <Cummings NPI>`, the Mayo `sl_code`, and `pytest`.

Total about 15 h engineering plus the rebuild window.
