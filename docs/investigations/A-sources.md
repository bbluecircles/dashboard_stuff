# Investigation A — moving the spine and affiliations to master_ref

Repo: `/home/user/dashboard_stuff`. No DB access; every "confirm" query below is for the user to run.

## A. Reading master_ref safely from another server

**Why a copy, not a join.** MariaDB cannot join across servers, and every consumer today is server-side SQL: `spine.spine_select_sql` (`provider_directory/spine.py`) selects straight from `{claims}.physician`; `analytics.rebuild_analytics` `org_sql` joins `{claims}.physician_primary_affiliation`; `affiliations.rebuild_org_lists` joins `{claims}.physician_affiliation_p/_i`. So the remote tables must land in the mart first as `az_pd.pd_src_physician` and `az_pd.pd_src_group_practice`, and the three SQL builders then point at `{mart}.pd_src_*`.

**Connection settings** (mirror `DB_*` in `db.py:db_config` and `PD_*` in `settings.py`):

| Var | Default | Use |
|---|---|---|
| `REF_DB_HOST` / `REF_DB_PORT` | none / 3306 | master_ref server; when `REF_DB_HOST` unset fall back to `DB_HOST` (same-server dev) |
| `REF_DB_USER` / `REF_DB_PASSWORD` | fall back to `DB_USER/DB_PASSWORD` | read-only account |
| `PD_REF_DB` | `master_ref` | schema name, validated by `require_ident` |
| `PD_REF_PHYSICIAN_TABLE` / `PD_REF_GROUP_TABLE` | `111_physician` / `cms_group_practice` | always backquoted via `quote_ident` (leading digit) |
| `PD_REF_CHUNK` | 50000 | keyset page size |

Add `ref_db_config()` and `get_ref_connection()` to `db.py`; it must open with `autocommit=True`, `cursorclass=DictCursor`, `read_timeout` set, and run `SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED` plus `SET SESSION max_statement_time = 30` as a guard. Autocommit means each SELECT is its own statement with no open snapshot, so InnoDB purge is not held back and no row/gap lock is ever taken (plain SELECT in InnoDB never locks). If the remote table is **MyISAM**, every SELECT takes a table-level READ lock for the statement's duration and blocks writers, which is exactly why reads must be short keyset pages rather than one streaming scan. Confirm engine and key first:

```sql
SELECT ENGINE, TABLE_ROWS, DATA_LENGTH FROM information_schema.TABLES
 WHERE TABLE_SCHEMA='master_ref' AND TABLE_NAME IN ('111_physician','cms_group_practice');
SHOW INDEX FROM master_ref.`111_physician`;
SHOW INDEX FROM master_ref.cms_group_practice;
SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA='master_ref' AND TABLE_NAME='111_physician'
   AND COLUMN_NAME IN ('physician_code','group_prac_code','affiliation_code','rec_modify_date');
```

**Keyed range scan.** If `physician_code` is indexed (PK expected), page by keyset, which is a BETWEEN with a moving lower bound and tolerates uneven density better than fixed-width ranges:

```sql
SELECT physician_code, npi_type, first_name, middle_name, last_name, suffix, degree_abbr,
       npi_spec_grp_code, npi_spec_grp_name, taxonomy_cat, taxonomy_cat_name, state_abbr,
       group_prac_code, group_prac_name, affiliation_code, affiliation_name, affiliation_dba_name,
       street, city, zip_code, phone_number, latitude, longitude, rec_modify_date
  FROM master_ref.`111_physician`
 WHERE physician_code > %s AND physician_code <= %s      -- (last_seen, hard upper bound)
 ORDER BY physician_code LIMIT %s;
```

Bounds come from one `SELECT MIN(physician_code), MAX(physician_code)`. Page `cms_group_practice` on `rec_id` the same way; if `rec_id` is not indexed, ask the owner for one.

**Mart-side write.** Follow `cms/load.py:_stream_insert` (executemany in `BATCH=1000`, `conn.commit()` every 5000) rather than the Phase 2 `MOD(npi,16)` buckets, because this is a row stream, not a set-based INSERT…SELECT. Load into `pd_src_physician_new`, verify `COUNT(*)` against the remote count taken at start, then `RENAME TABLE pd_src_physician TO pd_src_physician_old, pd_src_physician_new TO pd_src_physician` (atomic, works for MyISAM) and drop the old one. Readers never see a half table and a failed copy leaves the previous copy in place.

**MyISAM note.** `schema.table_options()` emits `ENGINE=MyISAM` and `convert_engine` forces it, so the mart-side `commit()` calls are no-ops for durability: a crash mid-load leaves a table needing `REPAIR TABLE`, which the swap pattern isolates. Use `ALTER TABLE … DISABLE KEYS` before the bulk insert and `ENABLE KEYS` after (MyISAM-only speedup). The locking concern is entirely on the **remote** side; the mart's engine does not change it.

**DDL (add to `schema.TABLES`/`ddl_statements`):** `pd_src_physician` with the columns above, `PRIMARY KEY (physician_code)`, `KEY idx_type_state (npi_type, state_abbr)`, `KEY idx_group (group_prac_code)`; `pd_src_group_practice` mirroring the CMS columns in snake_case (`group_pac_id VARCHAR(16)`, `group_enrollment_id`, `group_legal_business_name VARCHAR(180)`, `group_state_code CHAR(2)`, `group_due_date`, `group_reassignments INT`, `record_type`, `individual_enrollment_id`, `individual_npi BIGINT UNSIGNED`, …, `rec_id BIGINT PRIMARY KEY`, `KEY idx_npi (individual_npi)`, `KEY idx_pac (group_pac_id, group_state_code)`); plus `pd_src_state (source VARCHAR(32) PK, remote_rows, copied_rows, remote_max_modify DATETIME, copied_at)` so sync can be a no-op when the remote has not changed.

**Cadence and phase order.** New module `provider_directory/ref_copy.py` with `copy_ref_tables(ref_conn, mart_conn)`. It becomes a "Phase 0" step called at the top of `pipeline.run_phase1` and a new `sync --ref` clock in `sync.py`; the existing `spine` clock (`run_sync` sets `spine=True` whenever `claims` runs) should call the copy before `upsert_spine`. `111_physician` has `rec_modify_date`, so after the first full copy sync can skip when `MAX(rec_modify_date)` and `COUNT(*)` are unchanged (one cheap remote statement). Suggested schedule: weekly `sync --ref` (minutes of work), plus always before `phase4`. Phase 4 must raise (like `locations.Phase2Required`) if `pd_src_physician` is empty.

## B. Column mapping and what is lost

**Identity (`spine_select_sql`, `upsert_spine_*`):** keep the filters, swap the source.

| pd_provider | today (`az.physician`) | new (`pd_src_physician`) |
|---|---|---|
| npi | physician_code, `npi_type='1'`, `NOT IN (0,4)`, `state_abbr<>'XX'`, NPI range | same columns exist; same filters |
| first/middle/last_name, suffix | same | same |
| credential | degree_abbr | degree_abbr (degree_code/degree_name also available) |
| primary_specialty_code | npi_spec_grp_code | npi_spec_grp_code |
| primary_specialty_description | `azal.npi_spec_grp` name else physician.npi_spec_grp_name | same join still works: `azal` is on the mart server |
| specialty_classification | taxonomy_cat_name | taxonomy_cat_name |
| name_source | 'claims' | rename to 'ref' (field_help text) |
| gender, medical_school_*, estimated_age, gender/school_source | CMS PDC/NPPES only | **not in 111_physician**; stays CMS |
| in_system_provider, pd_network_npi | cms_pdc_facility_affil | unchanged |

**CMS decision:** keep `overlay_cms` and the PDC/NPPES loads. They never decided spine membership (that was `az.physician`); they fill gender, school, graduation year, estimated age, in_system, and, critically for section C, `pd_npi_xwalk.ind_pac_id/org_pac_id`, the only PAC-id signal the mart has. Only the README wording "spine from az.physician plus CMS" changes.

**Primary organization (`analytics.org_sql`):** replace the `physician_primary_affiliation` join:

```sql
UPDATE {mart}.pd_provider p
INNER JOIN {mart}.pd_src_physician s ON s.physician_code = p.npi
SET p.primary_organization_id   = CASE WHEN s.group_prac_code LIKE '888__________' /* 13 chars */
                                       THEN CAST(SUBSTRING(s.group_prac_code, 4) AS UNSIGNED)
                                       WHEN s.group_prac_code NOT IN ('0','') THEN CAST(s.group_prac_code AS UNSIGNED) END,
    p.primary_organization_npi  = <same expression>,
    p.primary_organization_name = CASE WHEN UPPER(TRIM(s.group_prac_name)) NOT IN ('UNKNOWN GROUP PRACTICE','UNKNOWN','')
                                       THEN LEFT(TRIM(s.group_prac_name),180) END,
    p.primary_organization_parent_id = NULL, p.primary_organization_parent_name = NULL   -- see D
WHERE MOD(p.npi, 16) = %s
```

Confirm the `'888'+NPI` convention and grain before coding:

```sql
SELECT COUNT(*), COUNT(DISTINCT physician_code) FROM master_ref.`111_physician`;   -- equal => one row per NPI
SELECT npi_type, LEFT(group_prac_code,3) AS pfx, CHAR_LENGTH(group_prac_code) AS len, COUNT(*)
  FROM master_ref.`111_physician` GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20;
SELECT COUNT(*) FROM master_ref.`111_physician` t1
  JOIN master_ref.`111_physician` t2 ON t2.physician_code = CAST(SUBSTRING(t1.group_prac_code,4) AS UNSIGNED)
 WHERE t1.npi_type='1' AND t2.npi_type='2';                       -- how many group codes resolve to a Type 2 row
SELECT COUNT(*), SUM(affiliation_code<>''), SUM(affiliation_code=group_prac_code) FROM master_ref.`111_physician` WHERE npi_type='1';
```

**Lost versus today, and where it could still come from:**

- `primary_organization_parent_id/_name` (IP/OP_hospital_system_* on `physician_primary_affiliation`): nothing equivalent in either new table. Options in D.
- `pd_provider_group_practice.billing_type`, `cases`, `max_period_code`, and the window filter `max_period_code >= window_start`: `cms_group_practice` has no claims volume. Keep the columns (models/API already allow NULL); optionally enrich them by joining `az.physician_affiliation_p/_i` on `organization_npi` when the crosswalk resolves an NPI, if the warehouse tables remain readable.
- The `p` vs `i` primary choice (`p_provider_billing_code` else `i_`): collapses to one `group_prac_code`.
- Group practice lists limited to the frozen window: PECOS reassignments are current-enrollment, not windowed.
- Gained: `street/city/zip_code/phone_number/latitude/longitude` per NPI (a fallback practice site for referring-only NPIs that Phase 3 leaves empty), `specialty_grp_*`, `pna_specialty_grp_*`, `inpatient/outpatient/ed/claim` flags, `rec_modify_date`.

Also needed: a prune step. `upsert_spine` only inserts/updates; NPIs that leave `111_physician` would linger. Add a bucketed `DELETE FROM pd_provider WHERE npi NOT IN (SELECT physician_code FROM pd_src_physician …)` (with child tables) behind a `--prune` flag, and decide with the owner whether removal is wanted.

## C. Group practices from cms_group_practice

**Identity mismatch, stated plainly.** Today `organization_id` everywhere is a Type 2 billing NPI (`pd_provider.primary_organization_id`, `pd_provider_group_practice.organization_id`, `pd_group_practice` PK `(member_scope, organization_id)`, `idx_org`, `group_profile.*` filters on `p.primary_organization_id = %s`, API path `/v1/group-practices/{organization_id}` typed `int`). `111_physician.group_prac_code` stays in that NPI namespace (`'888'+NPI`). `cms_group_practice.Group PAC ID` is a PECOS Associate Control ID: 10 digits, one per enrolled entity, an entity can hold several NPIs, and a 10-digit PAC id can numerically collide with a 10-digit NPI. The two cannot be unioned into one BIGINT column without a kind marker.

**Crosswalk `pd_org_xwalk (group_pac_id VARCHAR(16) PK, organization_npi BIGINT NULL, legal_name, match_method, votes, share, refreshed_at)`**, built in this order:

1. Authoritative: the CMS "Medicare Fee-For-Service Public Provider Enrollment" base file (data.cms.gov; columns `NPI`, `PECOS_ASCT_CNTL_ID`, `ENRLMT_ID`, `PROVIDER_TYPE_CD`, `ORG_NAME`). The reassignment sub-file *is* `cms_group_practice`, so `Group Enrollment ID` joins the base file to an NPI directly. Load it as `pd_src_pecos_enrollment` (or ask master_ref's owner to add it beside `cms_group_practice`).
2. Vote fallback when (1) is unavailable: for every spine NPI pair its `cms_group_practice.Group PAC ID`s with its `111_physician` org NPI and with `pd_npi_xwalk.org_pac_id`; count NPIs supporting each `(pac, npi)`; accept with votes ≥ 3 and share ≥ 0.6.
3. Tie-break on `UPPER(Group Legal Business Name)` vs `UPPER(group_prac_name)` / `strip_orgname`.

Confirm shape first:

```sql
SELECT COUNT(*), COUNT(DISTINCT `Individual NPI`), COUNT(DISTINCT `Group PAC ID`) FROM master_ref.cms_group_practice;
SELECT `Individual NPI`, COUNT(DISTINCT `Group PAC ID`) n FROM master_ref.cms_group_practice GROUP BY 1 ORDER BY n DESC LIMIT 10;
SELECT `Group State Code`, COUNT(*) FROM master_ref.cms_group_practice GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
SELECT COUNT(*) FROM master_ref.cms_group_practice g JOIN master_ref.`111_physician` p
    ON p.physician_code = g.`Individual NPI` WHERE p.npi_type='1';   -- coverage of the spine
```

**Keying.** Keep `organization_id BIGINT` as the directory key so the API, models, `pd_group_practice` PK and `group_profile` queries survive, and add `organization_pac_id VARCHAR(16)` + `organization_id_kind CHAR(3)` ('npi'/'pac') to `pd_provider`, `pd_provider_group_practice` and `pd_group_practice`. Rule: `organization_id` = crosswalked NPI when known; for unresolved PAC groups use `10000000000 + pac_id` (11 digits, above `NPI_MAX`, reversible, no collision). Document in `field_help` and `docs/ui-agent-spec.md` that `organization_id` is a key, not always an NPI (`organization_npi` is NULL in that case).

**`rebuild_org_lists` rewrite.** Replace `p_sql`/`i_sql` with one insert from `pd_src_group_practice` (filter `group_state_code = market_state` first; keep out-of-state rows ranked last rather than dropped), `header_sql` unchanged (keeps the 111_physician primary even when PECOS lacks it), and the rank order:

```
is_primary DESC,                          -- 1 when organization_id = p.primary_organization_id (111_physician)
(organization_pac_id = x.org_pac_id) DESC, -- PDC's listed org for the NPI (pd_npi_xwalk)
(group_state_code = market_state) DESC,
group_reassignments DESC, organization_id
```

If `111_physician` says group 0 / Unknown, `primary_organization_*` on `pd_provider` is set from rank 1 of that list in a second pass (so the directory still has the NPI as a member somewhere). Drop `_affiliation_name_ok_sql`'s person-name test only for PECOS rows (legal business names of solo groups are person names and are legitimate).

**Directory impact.** `group_scored_sql`/`group_member_where` stay keyed on `primary_organization_id`; counts change because membership is now "111_physician group, else top PECOS group". `parent_name` goes NULL unless D supplies it. `idx_scope_parent` remains valid.

## D. Scottsdale Healthcare Corp → Yavapai

**Lineage today.** `analytics.rebuild_analytics` `org_sql`: `primary_organization_parent_name = COALESCE(NULLIF(TRIM(a.IP_hospital_system_name),''), NULLIF(TRIM(a.OP_hospital_system_name),''))` per NPI from `az.physician_primary_affiliation`. That column is not "the parent of the billing org"; it is the hospital system where that clinician's inpatient (else outpatient) claims landed. Then `lookup.group_scored_sql` collapses members with `MAX(p.primary_organization_parent_name) AS parent_name` into `pd_group_practice` (`group_summary.rebuild_group_summary`). `MAX` on a VARCHAR is alphabetical, so one member of SCOTTSDALE HEALTHCARE CORP whose inpatient work was at YAVAPAI REGIONAL MEDICAL CENTER wins over any number of members tagged HONORHEALTH, because 'Y' > 'H'. `affiliations.parent_sql` also copies this value into `pd_provider_hospital_affiliation` rank 1 when the NPI has no SL-based system. `field_help.py:332-333, 386-387` document exactly this. The post-acquisition direction (Scottsdale/HonorHealth now owns Yavapai) is simply absent from the warehouse column.

Show the raw rows:

```sql
SELECT organization_id, organization_name, parent_id, parent_name, provider_count
  FROM az_pd.pd_group_practice WHERE member_scope='all' AND organization_name LIKE 'SCOTTSDALE HEALTHCARE CORP%';
SELECT p.primary_organization_parent_id, p.primary_organization_parent_name, COUNT(*)
  FROM az_pd.pd_provider p WHERE p.primary_organization_name LIKE 'SCOTTSDALE HEALTHCARE CORP%' GROUP BY 1,2 ORDER BY 3 DESC;
SELECT a.rendering_physician_code, a.p_provider_billing_code, a.p_dba_name, a.i_provider_billing_code, a.I_dba_name,
       a.IP_hospital_system_code, a.IP_hospital_system_name, a.OP_hospital_system_code, a.OP_hospital_system_name
  FROM az.physician_primary_affiliation a
 WHERE a.IP_hospital_system_name LIKE 'YAVAPAI%' AND (a.p_dba_name LIKE 'SCOTTSDALE HEALTHCARE%' OR a.I_dba_name LIKE 'SCOTTSDALE HEALTHCARE%') LIMIT 20;
```

**Do the new sources carry a parent?** No. `cms_group_practice` is flat (group ↔ individual). `111_physician.affiliation_code/affiliation_name/affiliation_dba_name` are the only candidate; the samples (`'888'+NPI`, `'NAME-NPI'`) look like another Type 2 org reference in the same `group_prac_code` namespace, which could be either a parent entity or a second billing org. Test it:

```sql
SELECT affiliation_code, affiliation_name, affiliation_dba_name, COUNT(*) FROM master_ref.`111_physician`
 WHERE affiliation_code<>'' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 30;
SELECT p.group_prac_name, p.affiliation_name, COUNT(*) FROM master_ref.`111_physician` p
 WHERE p.npi_type='1' AND p.affiliation_code<>'' AND p.affiliation_code<>p.group_prac_code GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;
SELECT group_prac_name, affiliation_name FROM master_ref.`111_physician` WHERE group_prac_name LIKE 'SCOTTSDALE HEALTHCARE%' OR group_prac_name LIKE 'YAVAPAI%' GROUP BY 1,2;
```

If `affiliation_*` is a consistent many-to-one over groups (few distinct affiliation codes per group_prac_code), map it to `primary_organization_parent_id/_name` (parent id = the 888-stripped NPI). If it is sparse or many-to-many, set parent columns NULL from the pipeline and add a small curated table `pd_org_parent_override (organization_id PK, parent_id, parent_name, note)` applied last in `org_sql`; the UI already hides hospital-system wording (`docs/ui-changes-urgent.md`). Either way, replace `MAX(parent_name)` in `group_scored_sql` with the modal value (`SUBSTRING_INDEX(GROUP_CONCAT(... ORDER BY cnt DESC), ',', 1)` over a per-org subquery) so a single outlier member cannot define a group's parent.

## E. Implementation plan

Rerun order after code lands: `sync --ref` (copy) → `sync --spine` (upsert, never `phase1`, so Phase 2–5 columns survive) → `phase4` (org + lists + group summary) → `phase6` (indexes, `pd_group_practice`) → `field-help` fixtures. `phase1` is only needed on a fresh mart. Run `--prune` once the owner agrees NPIs absent from 111_physician should drop.

| # | Step | Files | Hours |
|---|---|---|---|
| 1 | `REF_DB_*`/`PD_REF_*` settings, `ref_db_config`, `get_ref_connection` (autocommit, READ COMMITTED, statement cap) | `settings.py`, `db.py`, `.env` doc in README | 2 |
| 2 | DDL for `pd_src_physician`, `pd_src_group_practice`, `pd_src_state`, `pd_org_xwalk`, `pd_org_parent_override`; new columns `organization_pac_id`, `organization_id_kind` on three tables; `migrate_*` helpers; `TABLES` | `schema.py` | 3 |
| 3 | `ref_copy.py`: keyset paging, batch insert with `DISABLE/ENABLE KEYS`, count check, rename swap, state row, change detection | new module | 6 |
| 4 | Spine from `pd_src_physician` (`spine_select_sql`, `name_source='ref'`), prune option | `spine.py` | 3 |
| 5 | Wire copy into `run_phase1`, `sync --ref` clock, spine clock calls copy first, `Phase0Required` guard in `run_phase4`; CLI flags; `scripts/pd-sync.ps1` entry | `pipeline.py`, `sync.py`, `cli.py`, scripts | 3 |
| 6 | `org_sql` from `pd_src_physician` (888-strip, Unknown filter, parent NULL/override) | `analytics.py` | 3 |
| 7 | Crosswalk builder (enrollment file loader + vote fallback + name tie-break) | new `org_xwalk.py`, `cms/parse.py` for the enrollment CSV | 6 |
| 8 | `rebuild_org_lists` on `pd_src_group_practice` + crosswalk + new primary rule; second-pass primary for Unknown groups | `affiliations.py` | 8 |
| 9 | Modal parent in `group_scored_sql`; `organization_id_kind` in models/API docs | `lookup.py`, `group_summary.py`, `models.py`, `docs/ui-agent-spec.md` | 3 |
| 10 | `TABLE_REGISTRY` (`ref.111_physician`, `ref.cms_group_practice`, `pd_src_*`, `pd_org_xwalk`), field text, regenerate fixtures | `field_help.py`, `docs/fixtures/*` | 4 |
| 11 | Tests: `test_spine.py` (source is `pd_src_physician`, no `az.physician`), `test_affiliations.py` (drop `physician_affiliation_p/_i` asserts, add PECOS/xwalk asserts, primary order), `test_analytics.py` (`physician_primary_affiliation` gone), `test_sync.py` (ref clock, copy before upsert, never phase1), `test_field_help.py` built_from lineage, new `test_ref_copy.py` (page bounds, swap on count match only, autocommit on ref conn), `test_group_summary.py` (modal parent) | `tests/` | 6 |
| 12 | Dry run on dev: copy timings, remote lock check (`SHOW PROCESSLIST` on master_ref during copy), compare `pd_group_practice` before/after, verify Scottsdale row | — | 4 |

Total ≈ 51 hours. Steps 1–5 ship first (spine moves with no visible change); 6–9 are the affiliation cut-over and need the crosswalk queries in C answered before the design is locked.
