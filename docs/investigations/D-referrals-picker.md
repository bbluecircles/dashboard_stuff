# D. Referral peers and the group picker

Repo: `/home/user/dashboard_stuff/provider_directory`. No DB access; all claims below are from code. Queries use `az_pd` (mart) and `az_cms` (claims); set `@npi` / `@org` first.

## 1. Referral peers that are not actionable

### How peers are built today

- **Edges** — `complete.py:_fill_referral_edges` (lines 222–281). Per bucket, `INSERT ... SELECT d.rendering_physician_code, 'in', d.implied_all_referring_physician_code, SUM(d.patient_count), SUM(d.claim_count) FROM az_cms.dash_physician_referrals_to_rendering d INNER JOIN pd_provider rend ... INNER JOIN pd_provider refr ... WHERE d.period_code IN (window) AND both NPIs NOT IN (0,4) AND rendering <> referring AND rendering_npi_type = 1 AND implied_all_referring_npi_type = 1` into `pd_stg_referral_edge (npi, direction, peer_npi, patient_count, claim_count)`; mirrored for `'out'`. The only exclusions are dummy NPIs, self-edges, Type 2, and NPIs absent from `pd_provider`.
- **Ranking** — `complete.py:_rank_referrals` (284–330): `ROW_NUMBER() OVER (PARTITION BY e.npi, e.direction ORDER BY e.patient_count DESC, e.claim_count DESC, e.peer_npi)` → `WHERE rk <= 3` (`settings.MAX_REFERRAL_PEERS = 3`) into `pd_provider_referral`, joining `pd_provider peer` for `peer_name`/`peer_specialty`.
- **Lifecycle** — `pd_stg_referral_edge` is in `schema.PHASE5_STAGING_TABLES`, so `rebuild_complete` drops it at the start of every Phase 5 run (`complete.py:131 drop_phase5_staging`). It survives *between* runs, but is treated as disposable.
- **Group profile** — `group_profile.py:fetch_group_referrals` (375–420): `FROM pd_provider p INNER JOIN pd_provider_referral r ON r.npi = p.npi WHERE p.primary_organization_id = %s GROUP BY r.direction, r.peer_npi` summing `patient_count`, then `ROW_NUMBER() ... PARTITION BY g.direction ORDER BY patient_count DESC` top 3. So group peers are a **sum of each primary member's already-truncated top 3**, and a peer who is himself a member of the org is never excluded. A contracted pathologist/radiologist who is the #1 "out" peer of 40 members wins by construction.

### Filters, in order of confidence (mart only)

Let `me` = `pd_provider` row for `r.npi`, `pp` = `pd_provider` row for `r.peer_npi`.

| # | Rule | SQL predicate (exclude when true) | Confidence |
|---|------|-----------------------------------|------------|
| 1 | Same primary billing org | `pp.primary_organization_id IS NOT NULL AND pp.primary_organization_id = me.primary_organization_id` | High — the NP-in-same-practice case |
| 2 | Share any in-window org | `EXISTS (SELECT 1 FROM pd_provider_group_practice a JOIN pd_provider_group_practice b ON b.organization_id = a.organization_id WHERE a.npi = me.npi AND b.npi = pp.npi)` | High; later OR with `master_ref.cms_group_practice` (Individual NPI → Group PAC ID) once loaded |
| 3 | Non-actionable specialty | `pp.primary_specialty_description IN (list) OR pp.specialty_classification IN (list)` with a configurable `REFERRAL_EXCLUDED_SPECIALTIES` (Pathology, Diagnostic Radiology, Anesthesiology, Clinical Laboratory, Nurse Practitioner, Physician Assistant, Student). Use `LIKE` on trimmed/upper values — exact strings in the mart are not known | Medium — removes the pathology/radiology peers but also legitimate radiology referral targets; keep the list in settings |
| 4 | Same primary site | `ms.cluster_key = ps.cluster_key` where `ms`/`ps` = `pd_provider_practice` rows with `site_rank = 1` | Medium-low; catches employees whose billing org differs (hospital-employed) but also co-located independents |

Group-profile exclusion (member of the group itself): `pp.primary_organization_id = @org OR EXISTS (SELECT 1 FROM pd_provider_group_practice g WHERE g.npi = pp.npi AND g.organization_id = @org)`, plus rule 3.

### Quantify for one NPI

```sql
SET @npi := 1234567890;
SELECT r.direction, r.peer_rank, r.peer_npi, r.peer_name, r.peer_specialty, r.patient_count,
  (pp.primary_organization_id IS NOT NULL
     AND pp.primary_organization_id = me.primary_organization_id)        AS r1_same_primary_org,
  EXISTS (SELECT 1 FROM az_pd.pd_provider_group_practice a
          JOIN az_pd.pd_provider_group_practice b ON b.organization_id = a.organization_id
          WHERE a.npi = r.npi AND b.npi = r.peer_npi)                     AS r2_shared_group,
  (UPPER(pp.primary_specialty_description) REGEXP 'PATHOLOG|DIAGNOSTIC RADIOLOGY|ANESTHESIOLOGY|CLINICAL LABORATORY|NURSE PRACTITIONER|PHYSICIAN ASSISTANT|STUDENT'
   OR UPPER(pp.specialty_classification) REGEXP 'PATHOLOG|DIAGNOSTIC RADIOLOGY|ANESTHESIOLOGY|CLINICAL LABORATORY|NURSE PRACTITIONER|PHYSICIAN ASSISTANT|STUDENT') AS r3_specialty,
  (ps.cluster_key IS NOT NULL AND ps.cluster_key = ms.cluster_key)        AS r4_same_site
FROM az_pd.pd_provider_referral r
JOIN az_pd.pd_provider me ON me.npi = r.npi
JOIN az_pd.pd_provider pp ON pp.npi = r.peer_npi
LEFT JOIN az_pd.pd_provider_practice ms ON ms.npi = r.npi      AND ms.site_rank = 1
LEFT JOIN az_pd.pd_provider_practice ps ON ps.npi = r.peer_npi AND ps.site_rank = 1
WHERE r.npi = @npi
ORDER BY r.direction, r.peer_rank;
```

Counts per rule: wrap the above in `SELECT direction, SUM(r1_same_primary_org), SUM(r2_shared_group), SUM(r3_specialty), SUM(r4_same_site), COUNT(*) FROM (...) x GROUP BY direction`.

What top 5 would look like after filtering, from the unfiltered edge table (run only after a Phase 5 so `pd_stg_referral_edge` exists):

```sql
SELECT * FROM (
  SELECT e.direction, e.peer_npi, pp.last_name, pp.primary_specialty_description, e.patient_count,
         ROW_NUMBER() OVER (PARTITION BY e.direction ORDER BY e.patient_count DESC, e.claim_count DESC, e.peer_npi) AS new_rank
  FROM az_pd.pd_stg_referral_edge e
  JOIN az_pd.pd_provider me ON me.npi = e.npi
  JOIN az_pd.pd_provider pp ON pp.npi = e.peer_npi
  WHERE e.npi = @npi
    AND NOT (pp.primary_organization_id IS NOT NULL AND pp.primary_organization_id = me.primary_organization_id)
    AND NOT EXISTS (SELECT 1 FROM az_pd.pd_provider_group_practice a
                    JOIN az_pd.pd_provider_group_practice b ON b.organization_id = a.organization_id
                    WHERE a.npi = e.npi AND b.npi = e.peer_npi)
    AND UPPER(IFNULL(pp.primary_specialty_description,'')) NOT REGEXP 'PATHOLOG|DIAGNOSTIC RADIOLOGY|ANESTHESIOLOGY|CLINICAL LABORATORY|NURSE PRACTITIONER|PHYSICIAN ASSISTANT|STUDENT'
) x WHERE x.new_rank <= 5 ORDER BY x.direction, x.new_rank;
```

Raw warehouse check for the same NPI (incoming):

```sql
SELECT d.implied_all_referring_physician_code AS peer, SUM(d.patient_count) pts, SUM(d.claim_count) clms
FROM az_cms.dash_physician_referrals_to_rendering d
WHERE d.rendering_physician_code = @npi AND d.rendering_npi_type = 1 AND d.implied_all_referring_npi_type = 1
  AND d.period_code BETWEEN 202308 AND 202407
GROUP BY peer ORDER BY pts DESC LIMIT 10;
```

Group: how many of the group's current top peers are members:

```sql
SET @org := 1234567893;
SELECT r.direction, r.peer_npi, SUM(r.patient_count) pts,
  MAX(pp.primary_organization_id = @org) AS peer_is_primary_member,
  MAX(EXISTS (SELECT 1 FROM az_pd.pd_provider_group_practice g WHERE g.npi = r.peer_npi AND g.organization_id = @org)) AS peer_is_any_member,
  MAX(pp.primary_specialty_description) spec
FROM az_pd.pd_provider p JOIN az_pd.pd_provider_referral r ON r.npi = p.npi
JOIN az_pd.pd_provider pp ON pp.npi = r.peer_npi
WHERE p.primary_organization_id = @org
GROUP BY r.direction, r.peer_npi ORDER BY r.direction, pts DESC LIMIT 20;
```

Distinct specialty strings the list must match: `SELECT primary_specialty_description, specialty_classification, COUNT(*) FROM az_pd.pd_provider GROUP BY 1,2 ORDER BY 3 DESC;`

### Where the filter belongs

- **Not at edge build.** Keep `pd_stg_referral_edge` unfiltered and complete; it is the only thing that lets the rules change without rescanning `az_cms` (the expensive part of Phase 5).
- **At ranking** (`_rank_referrals`), with the exclusions as `WHERE NOT (...)` inside the `ranked` subquery so rank 1..5 is computed *after* exclusion. Top-N is stored, so read-time filtering of `pd_provider_referral` would leave holes (filter 2 of 3 and show 1).
- **Make the edge table durable**: move `pd_stg_referral_edge` from `PHASE5_STAGING_TABLES` to `PHASE5_CACHED_STAGING_TABLES` (or rename to `pd_referral_edge`) and have `_fill_referral_edges` skip when `reuse_cached` and the table has rows, like `_fill_visit_dates`. Add a CLI `referrals-rerank` (new `rebuild_referral_ranks(conn)` that truncates `pd_provider_referral` and reruns only `_rank_referrals`). Re-ranking from the edge table is minutes, not a Phase 5.
- **Group profile**: either (a) keep the read-time roll-up but add the member-exclusion and specialty predicates to `fetch_group_referrals` (still a lower bound of truncated per-member lists), or (b) better, add a Phase 5 step `_rank_group_referrals` that aggregates `pd_stg_referral_edge` across primary members per `organization_id`, excludes member peers, and stores top 5 in a new `pd_group_referral (organization_id, direction, peer_rank, ...)`; `fetch_group_referrals` then reads it. (b) is correct, not truncated, and cheap.
- **Top 5**: `MAX_REFERRAL_PEERS = 5`; `fetch_group_referrals` already uses it. Update `field_help.py` lines 83, 374, 479 ("Top 3") and spec line 126/158/176. `ui-changes-urgent.md` already defers this to the data-rebuild branch.
- Optional read-time knob: `referral_filter=strict|none` on `GET /v1/providers/{npi}` only if an unfiltered `pd_provider_referral_raw` is kept; otherwise skip.

## 2. Picker bug

Flow: provider profile → `group_practices[]` entry → `GET /v1/group-practices/{id}` → "Browse providers" → `GET /v1/providers?state=&organization_id={id}&min_visits=1` + `last_name`.

**Cause A — confirmed: picker filters primary org only.** `lookup.py:_provider_filter_clauses` (432–434): `clauses.append(f"{p}primary_organization_id = %s")`. `pd_provider.primary_organization_id` is one org from `az_cms.physician_primary_affiliation` (`analytics.py:465–485`, `p_provider_billing_code` else `i_provider_billing_code`). But the profile's `group_practices[]` (`lookup.fetch_group_practices`, built by `affiliations.py:rebuild_org_lists` 128–262) holds *every* `physician_affiliation_p` / `_i` org with `max_period_code >= window_start`, ranked `is_primary` first; `is_primary = (t.organization_id = p.primary_organization_id)`. The spec (line 155) tells the UI to show "primary first, then every other in-window billing org". If the user clicked any row with `is_primary = false`, the picker with that `organization_id` cannot contain the provider. The link does pass `group_practices[].organization_id` (spec 112: "Dump members with ... organization_id={id}"). `test_groups.py:test_list_providers_sql_filters_organization_id` pins this behaviour.

**Cause B — confirmed as a possible contributor: `min_visits=1`.** `IFNULL(p.visits_total, 0) >= 1` (lookup.py:412). The API default is `None` (`api.py:296`); the `1` is the UI default from spec line 61. A referring-only or no-visit NPI (`visits_total` NULL/0) is excluded even when the org id is right. Which cause applies is data-dependent: run the query below.

**Cause C — group member set is primary-only, and consistent with the picker.** `lookup.group_member_where` → `p.primary_organization_id IS NOT NULL`, `group_scored_sql` → `GROUP BY p.primary_organization_id`; every `group_profile.py` query uses `WHERE p.primary_organization_id = %s`; `pd_group_practice` (`group_summary.py`) materialises the same. So `provider_count` equals the picker total with `min_visits` cleared and `active` unset. The inconsistency is only between **the provider profile's `group_practices[]` (any org)** and **the group grain (primary org)**. A non-primary org that is nobody's primary has no `pd_group_practice` row at all and the group route 404s (`api.py:346–350`), so the reported case is an org that is primary for *other* members but not for the starting provider.

Refuted: empty `state=` (`market_for_state("")` falls back to `MARKET_STATE`), paging (last-name filter), `_clamp_limit`.

Confirmation query:

```sql
SET @npi := 1234567890; SET @org := 1234567893;
SELECT p.npi, p.last_name, p.visits_total, p.active_provider,
       p.primary_organization_id, p.primary_organization_name,
       (p.primary_organization_id = @org)       AS passes_org_filter,
       (IFNULL(p.visits_total, 0) >= 1)        AS passes_min_visits_1,
       g.org_rank, g.organization_id, g.organization_name, g.is_primary, g.billing_type, g.cases, g.max_period_code
FROM az_pd.pd_provider p
LEFT JOIN az_pd.pd_provider_group_practice g ON g.npi = p.npi
WHERE p.npi = @npi ORDER BY g.org_rank;
-- group as the UI sees it, and the two member counts
SELECT member_scope, provider_count, visits_total FROM az_pd.pd_group_practice WHERE organization_id = @org;
SELECT (SELECT COUNT(*) FROM az_pd.pd_provider WHERE primary_organization_id = @org) AS primary_members,
       (SELECT COUNT(DISTINCT npi) FROM az_pd.pd_provider_group_practice WHERE organization_id = @org) AS any_members;
```

`passes_org_filter = 0` with a `g.organization_id = @org, is_primary = 0` row ⇒ Cause A. `passes_org_filter = 1, passes_min_visits_1 = 0` ⇒ Cause B.

### Fix

Add `membership: Literal["primary","any"] = "primary"` to `list_providers` / `_provider_filter_clauses` and `GET /v1/providers`. For `any`:

```sql
EXISTS (SELECT 1 FROM az_pd.pd_provider_group_practice g WHERE g.npi = p.npi AND g.organization_id = %s)
```

(`idx_npi_org (npi, organization_id)` already exists; for `COUNT(*)` use `idx_org`.) The UI's "Browse providers" sends `membership=any` and drops `min_visits=1` (or shows a "0 visits" toggle). Keep `pd_group_practice`, `group_scored_sql` and `group_profile.py` on primary-only so totals stay consistent, and return `member_count_any` in the group profile (one extra `COUNT(DISTINCT npi)` on `pd_provider_group_practice`) so the UI can caption "N billing members, M in totals". Add `is_primary_member` to `ProviderDumpRow` (`EXISTS ... AND g.is_primary = 1`) when `membership=any` so non-primary rows are visibly marked. Also have the profile link prefer the `is_primary` entry; navigating to a non-primary org that is nobody's primary still 404s — handle in the UI (disable link when the group route 404s) or add `pd_group_practice` rows for `member_scope='any'` later.

## 3. Implementation plan

| # | Step | Files / functions | Tests | Rerun | Hrs |
|---|------|-------------------|-------|-------|-----|
| 1 | Picker `membership=any\|primary`; `is_primary_member` column; `member_count_any` on group profile | `lookup.py:_provider_filter_clauses, list_providers`; `api.py:provider_search`; `models.py:ProviderDumpRow, GroupPracticeProfile`; `group_profile.py:attach_group_profile`; spec line 112; `field_help.py` | `tests/test_groups.py` (new clause asserts), `tests/test_api.py` | none (read-time) | 4 |
| 2 | Make edge table durable + `referrals-rerank` | `schema.py` (move `pd_stg_referral_edge` to cached), `complete.py:_fill_referral_edges` (skip when cached), new `rebuild_referral_ranks`; `cli.py`, `pipeline.py` | `tests/test_complete.py` | one Phase 5 to populate | 3 |
| 3 | Exclusion rules in `_rank_referrals`; `settings.REFERRAL_EXCLUDED_SPECIALTIES`; `MAX_REFERRAL_PEERS = 5` | `complete.py`, `settings.py`, `field_help.py` (83, 374, 479) | `tests/test_complete.py` (SQL contains each predicate; peers = 5) | `referrals-rerank` (after step 2) | 5 |
| 4 | `pd_group_referral` built in Phase 5 from the edge table, member-excluded, top 5; `fetch_group_referrals` reads it with live fallback | `schema.py`, `complete.py:_rank_group_referrals`, `group_profile.py` | `tests/test_group_profile.py` | `referrals-rerank` | 5 |
| 5 | Validate with the section-1/2 queries on the reported NPIs; tune specialty list; spec/field-help text | `docs/ui-agent-spec.md` | — | — | 2 |
| 6 | Later: load `master_ref.cms_group_practice`, add as rule 2b | `affiliations.py` or new loader | — | rerank | 4 |

Order: 1 (independent, ships now) → 2 → 3 → 4 → 5 → 6. Total ~19 h excluding Phase 5 runtime.
