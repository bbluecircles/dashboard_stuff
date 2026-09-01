"""Phase 4: wRVU, payer mix, primary org, work_type polish.

Reads az / azal / existing Phase 2–3 staging. Writes az_pd only.
Does not rescan az.pat_dt or drop Phase 2/3 tables.

wRVU is one HCPCS/CPT work procedure per visit × a plausible physician work
RVU from azal.procd (WORK_RVU when it looks real, else total − PE − MP).
Do not round site wRVU per encounter bucket — Galera splits visits across
16 hashes and ROUND(0.001, 2) zeros them out. Line-level procd_dt sums are
a later refinement. Payer mix reuses az.dash_physician_payor_all claim
counts in the frozen window. Code 5 Other is excluded from the four
percents. Top 3 payers are commercial parents only.
"""

from __future__ import annotations

from provider_directory.activity import iter_period_codes
from provider_directory.db import quote_ident
from provider_directory.locations import Phase2Required, is_person_practice_name_sql, table_has_rows
from provider_directory.schema import create_schema, drop_phase4_staging
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_COLLATION,
    MART_DB,
    PAYOR_COMMERCIAL,
    PAYOR_HMO_MA,
    PAYOR_MEDICAID,
    PAYOR_MEDICARE_FFS,
    PAYOR_MIX_CODES,
    WINDOW_END,
    WINDOW_START,
)
from provider_directory.transforms import (
    MIN_PLAUSIBLE_TOTAL_RVU,
    MIN_PLAUSIBLE_WORK_RVU,
    POS_WORK_TYPE,
)

PROVIDER_BUCKETS = 16
VISIT_BUCKETS = 16


def _session_timeouts(cur) -> None:
    cur.execute("SET SESSION wait_timeout = 28800")
    cur.execute("SET SESSION net_read_timeout = 28800")
    cur.execute("SET SESSION net_write_timeout = 28800")
    try:
        cur.execute("SET SESSION max_statement_time = 0")
    except Exception:
        pass


def _run(cur, conn, sql: str, params: tuple | None = None) -> int:
    cur.execute(sql, params or ())
    n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.commit()
    return n


def period_in_sql(start: int = WINDOW_START, end: int = WINDOW_END) -> str:
    periods = ", ".join(str(p) for p in iter_period_codes(start, end))
    return f"IN ({periods})"


def is_hcpcs_sql(expr: str) -> str:
    """Professional claims use 5-character CPT/HCPCS, not 7-character ICD-10-PCS."""
    return f"CHAR_LENGTH(TRIM({expr})) = 5"


def work_rvu_sql(alias: str = "pr") -> str:
    """Plausible physician work RVU. See physician_work_rvu() for the same rules."""
    a = alias
    min_w = MIN_PLAUSIBLE_WORK_RVU
    min_t = MIN_PLAUSIBLE_TOTAL_RVU
    return f"""
        COALESCE(
            CASE WHEN {a}.WORK_RVU >= {min_w} THEN {a}.WORK_RVU END,
            CASE
                WHEN {a}.NON_FACILITY_TOTAL IS NOT NULL
                 AND {a}.NON_FAC_PE_RVU IS NOT NULL
                 AND {a}.MP_RVU IS NOT NULL
                 AND ({a}.NON_FACILITY_TOTAL - {a}.NON_FAC_PE_RVU - {a}.MP_RVU) >= {min_w}
                THEN {a}.NON_FACILITY_TOTAL - {a}.NON_FAC_PE_RVU - {a}.MP_RVU
            END,
            CASE
                WHEN {a}.FACILITY_TOTAL IS NOT NULL
                 AND {a}.FACILITY_PE_RVU IS NOT NULL
                 AND {a}.MP_RVU IS NOT NULL
                 AND ({a}.FACILITY_TOTAL - {a}.FACILITY_PE_RVU - {a}.MP_RVU) >= {min_w}
                THEN {a}.FACILITY_TOTAL - {a}.FACILITY_PE_RVU - {a}.MP_RVU
            END,
            CASE
                WHEN {a}.nf_total_rvu >= {min_t}
                 AND ({a}.WORK_RVU IS NULL OR {a}.WORK_RVU < {min_w})
                THEN {a}.nf_total_rvu
            END,
            CASE WHEN {a}.WORK_RVU > 0 THEN {a}.WORK_RVU END
        )
    """


def work_type_case_sql(sl: str = "sl") -> str:
    pos_whens = "\n".join(
        f"WHEN {int(code)} THEN '{label}'" for code, label in sorted(POS_WORK_TYPE.items())
    )
    roll = f"LOWER(IFNULL({sl}.im_specialty_rollup, ''))"
    # CONCAT('%%', ...) so pymysql %s formatting leaves a SQL LIKE wildcard.
    return f"""
        COALESCE(
            CASE {sl}.pos_type_code
                {pos_whens}
                ELSE NULL
            END,
            CASE
                WHEN {roll} LIKE CONCAT('%%', 'urgent', '%%') THEN 'Urgent Care'
                WHEN {roll} LIKE CONCAT('%%', 'emergency', '%%') THEN 'Emergency Department'
                WHEN {roll} LIKE CONCAT('%%', 'ambulatory surg', '%%') THEN 'Ambulatory Surgery Center'
                WHEN {roll} LIKE CONCAT('%%', 'skilled nursing', '%%') THEN 'Skilled Nursing Facility'
                WHEN {roll} LIKE CONCAT('%%', 'hospice', '%%') THEN 'Hospice'
                WHEN {roll} LIKE CONCAT('%%', 'dialysis', '%%')
                  OR {roll} LIKE CONCAT('%%', 'end-stage', '%%') THEN 'End-Stage Renal Disease Facility'
                WHEN {roll} LIKE CONCAT('%%', 'acute care hospital', '%%')
                  OR {roll} LIKE CONCAT('%%', 'general acute', '%%') THEN 'Short Term Acute Care Hospital'
                WHEN {roll} LIKE CONCAT('%%', 'outpatient hospital', '%%') THEN 'Hospital Outpatient'
                WHEN {roll} LIKE CONCAT('%%', 'office specialty', '%%') THEN 'Single Specialty Group'
                WHEN {roll} LIKE CONCAT('%%', 'office', '%%') THEN 'Office'
                ELSE NULL
            END,
            NULLIF(TRIM({sl}.pos_type_name), ''),
            NULLIF(TRIM({sl}.im_specialty_rollup), '')
        )
    """


def rebuild_analytics(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
    window_start: int = WINDOW_START,
    window_end: int = WINDOW_END,
) -> dict:
    if not table_has_rows(conn, mart_db, "pd_stg_visit"):
        raise Phase2Required("pd_stg_visit is empty. Run phase2 first.")
    drop_phase4_staging(conn, mart_db)
    create_schema(conn, mart_db)

    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    mix = ", ".join(str(c) for c in PAYOR_MIX_CODES)
    period_in = period_in_sql(window_start, window_end)
    has_visit_site = table_has_rows(conn, mart_db, "pd_stg_visit_site")
    has_npi_sl = table_has_rows(conn, mart_db, "pd_stg_npi_sl")
    has_practice = table_has_rows(conn, mart_db, "pd_provider_practice")
    counts: dict[str, int] = {
        "wrvu_npi": 0,
        "site_wrvu_rows": 0,
        "payor_rows": 0,
        "providers_wrvu": 0,
        "providers_payer": 0,
        "providers_org": 0,
        "practices_wrvu": 0,
        "practices_work_type": 0,
        "practices_names": 0,
    }

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for bucket in range(PROVIDER_BUCKETS):
            _run(
                cur,
                conn,
                f"""
                UPDATE {mart}.pd_provider
                SET
                    wrvu_total = NULL,
                    wrvu_average = NULL,
                    wrvu_procedure_count = NULL,
                    visits_percent_third_party = NULL,
                    visits_percent_medicaid = NULL,
                    visits_percent_medicare_advantage = NULL,
                    visits_percent_medicare_traditional = NULL,
                    top_payer_name_1 = NULL,
                    top_payer_percent_1 = NULL,
                    top_payer_name_2 = NULL,
                    top_payer_percent_2 = NULL,
                    top_payer_name_3 = NULL,
                    top_payer_percent_3 = NULL,
                    primary_organization_id = NULL,
                    primary_organization_name = NULL,
                    primary_organization_npi = NULL,
                    primary_organization_parent_id = NULL,
                    primary_organization_parent_name = NULL
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}
                """,
            )
        if has_practice:
            for bucket in range(PROVIDER_BUCKETS):
                _run(
                    cur,
                    conn,
                    f"""
                    UPDATE {mart}.pd_provider_practice
                    SET wrvu_at_site = NULL, wrvu_share_pct = NULL
                    WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}
                    """,
                )

        wrvu_expr = work_rvu_sql("pr")
        wrvu_sql = f"""
            INSERT INTO {mart}.pd_stg_npi_wrvu (npi, total_wrvu, procedure_count)
            SELECT
                v.rendering_npi,
                ROUND(SUM({wrvu_expr}), 2),
                COUNT(*)
            FROM {mart}.pd_stg_visit v
            INNER JOIN {mart}.pd_provider p ON p.npi = v.rendering_npi
            INNER JOIN {lookup}.procd pr
                ON pr.procd_code = TRIM(v.px) COLLATE {MART_COLLATION}
            WHERE MOD(v.rendering_npi, {PROVIDER_BUCKETS}) = %s
              AND v.px IS NOT NULL AND v.px <> ''
              AND {is_hcpcs_sql("v.px")}
              AND {wrvu_expr} IS NOT NULL
            GROUP BY v.rendering_npi
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, wrvu_sql, (bucket,))
            counts["wrvu_npi"] += n
            print(f"phase4 wrvu npi bucket {bucket}: {n} rows", flush=True)

        overlay_wrvu_sql = f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN {mart}.pd_stg_npi_wrvu w ON w.npi = p.npi
            SET
                p.wrvu_total = w.total_wrvu,
                p.wrvu_procedure_count = w.procedure_count,
                p.wrvu_average = ROUND(w.total_wrvu / w.procedure_count, 3),
                p.refreshed_at = NOW()
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, overlay_wrvu_sql, (bucket,))
            counts["providers_wrvu"] += n
            print(f"phase4 wrvu overlay bucket {bucket}: {n} rows", flush=True)

        if has_visit_site:
            site_wrvu_sql = f"""
                INSERT INTO {mart}.pd_stg_site_wrvu (npi, sl_code, total_wrvu, procedure_count)
                SELECT
                    vs.rendering_npi,
                    vs.sl_code,
                    SUM({wrvu_expr}),
                    COUNT(*)
                FROM {mart}.pd_stg_visit_site vs
                INNER JOIN {mart}.pd_stg_visit v ON v.encounter_id = vs.encounter_id
                INNER JOIN {lookup}.procd pr
                    ON pr.procd_code = TRIM(v.px) COLLATE {MART_COLLATION}
                WHERE MOD(vs.encounter_id, {VISIT_BUCKETS}) = %s
                  AND v.px IS NOT NULL AND v.px <> ''
                  AND {is_hcpcs_sql("v.px")}
                  AND {wrvu_expr} IS NOT NULL
                GROUP BY vs.rendering_npi, vs.sl_code
                ON DUPLICATE KEY UPDATE
                    total_wrvu = {mart}.pd_stg_site_wrvu.total_wrvu + VALUES(total_wrvu),
                    procedure_count = {mart}.pd_stg_site_wrvu.procedure_count + VALUES(procedure_count)
            """
            for bucket in range(VISIT_BUCKETS):
                n = _run(cur, conn, site_wrvu_sql, (bucket,))
                counts["site_wrvu_rows"] += n
                print(f"phase4 site wrvu bucket {bucket}: {n} rows", flush=True)

        if has_practice and has_npi_sl:
            practice_wrvu_sql = f"""
                UPDATE {mart}.pd_provider_practice pr
                INNER JOIN (
                    SELECT
                        s.npi,
                        sl.cluster_key,
                        ROUND(SUM(s.total_wrvu), 2) AS wrvu
                    FROM {mart}.pd_stg_site_wrvu s
                    INNER JOIN {mart}.pd_stg_npi_sl sl
                        ON sl.npi = s.npi AND sl.sl_code = s.sl_code
                    WHERE MOD(s.npi, {PROVIDER_BUCKETS}) = %s
                    GROUP BY s.npi, sl.cluster_key
                ) x ON x.npi = pr.npi AND x.cluster_key = pr.cluster_key
                INNER JOIN {mart}.pd_provider p ON p.npi = pr.npi
                SET
                    pr.wrvu_at_site = x.wrvu,
                    pr.wrvu_share_pct = ROUND(100.0 * x.wrvu / NULLIF(p.wrvu_total, 0), 2)
                WHERE MOD(pr.npi, {PROVIDER_BUCKETS}) = %s
            """
            for bucket in range(PROVIDER_BUCKETS):
                n = _run(cur, conn, practice_wrvu_sql, (bucket, bucket))
                counts["practices_wrvu"] += n
                print(f"phase4 practice wrvu bucket {bucket}: {n} rows", flush=True)

        payor_sql = f"""
            INSERT INTO {mart}.pd_stg_npi_payor (
                npi, is_payor_code, payor_parent_name, claim_count
            )
            SELECT
                d.rendering_physician_code,
                d.is_payor_code,
                LEFT(COALESCE(
                    NULLIF(TRIM(py.payor_parent_name), ''),
                    NULLIF(TRIM(d.payor_name), ''),
                    'Unknown'
                ), 140),
                SUM(d.claim_count)
            FROM {claims}.dash_physician_payor_all d
            INNER JOIN {mart}.pd_provider p ON p.npi = d.rendering_physician_code
            LEFT JOIN {claims}.payor py
                ON py.payor_code = d.payor_code COLLATE {MART_COLLATION}
            WHERE d.period_code {period_in}
              AND d.rendering_physician_code NOT IN ({dummy})
              AND MOD(d.rendering_physician_code, {PROVIDER_BUCKETS}) = %s
            GROUP BY
                d.rendering_physician_code,
                d.is_payor_code,
                LEFT(COALESCE(
                    NULLIF(TRIM(py.payor_parent_name), ''),
                    NULLIF(TRIM(d.payor_name), ''),
                    'Unknown'
                ), 140)
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, payor_sql, (bucket,))
            counts["payor_rows"] += n
            print(f"phase4 payor bucket {bucket}: {n} rows", flush=True)

        mix_sql = f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN (
                SELECT
                    npi,
                    SUM(CASE WHEN is_payor_code = {PAYOR_MEDICARE_FFS} THEN claim_count ELSE 0 END) AS ffs,
                    SUM(CASE WHEN is_payor_code = {PAYOR_MEDICAID} THEN claim_count ELSE 0 END) AS medicaid,
                    SUM(CASE WHEN is_payor_code = {PAYOR_COMMERCIAL} THEN claim_count ELSE 0 END) AS commercial,
                    SUM(CASE WHEN is_payor_code = {PAYOR_HMO_MA} THEN claim_count ELSE 0 END) AS ma,
                    SUM(CASE WHEN is_payor_code IN ({mix}) THEN claim_count ELSE 0 END) AS denom
                FROM {mart}.pd_stg_npi_payor
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
                GROUP BY npi
            ) m ON m.npi = p.npi
            SET
                p.visits_percent_medicare_traditional = ROUND(100.0 * m.ffs / NULLIF(m.denom, 0), 2),
                p.visits_percent_medicaid = ROUND(100.0 * m.medicaid / NULLIF(m.denom, 0), 2),
                p.visits_percent_third_party = ROUND(100.0 * m.commercial / NULLIF(m.denom, 0), 2),
                p.visits_percent_medicare_advantage = ROUND(100.0 * m.ma / NULLIF(m.denom, 0), 2),
                p.refreshed_at = NOW()
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
        """
        top_sql = f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN (
                SELECT
                    npi,
                    MAX(CASE WHEN rk = 1 THEN payor_parent_name END) AS n1,
                    MAX(CASE WHEN rk = 1 THEN pct END) AS p1,
                    MAX(CASE WHEN rk = 2 THEN payor_parent_name END) AS n2,
                    MAX(CASE WHEN rk = 2 THEN pct END) AS p2,
                    MAX(CASE WHEN rk = 3 THEN payor_parent_name END) AS n3,
                    MAX(CASE WHEN rk = 3 THEN pct END) AS p3
                FROM (
                    SELECT
                        npi,
                        payor_parent_name,
                        ROUND(
                            100.0 * claim_count
                            / NULLIF(SUM(claim_count) OVER (PARTITION BY npi), 0),
                            2
                        ) AS pct,
                        ROW_NUMBER() OVER (
                            PARTITION BY npi
                            ORDER BY claim_count DESC, payor_parent_name
                        ) AS rk
                    FROM (
                        SELECT npi, payor_parent_name, SUM(claim_count) AS claim_count
                        FROM {mart}.pd_stg_npi_payor
                        WHERE is_payor_code = {PAYOR_COMMERCIAL}
                          AND MOD(npi, {PROVIDER_BUCKETS}) = %s
                        GROUP BY npi, payor_parent_name
                    ) commercial
                ) ranked
                WHERE rk <= 3
                GROUP BY npi
            ) t ON t.npi = p.npi
            SET
                p.top_payer_name_1 = t.n1,
                p.top_payer_percent_1 = t.p1,
                p.top_payer_name_2 = t.n2,
                p.top_payer_percent_2 = t.p2,
                p.top_payer_name_3 = t.n3,
                p.top_payer_percent_3 = t.p3
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
        """
        for bucket in range(PROVIDER_BUCKETS):
            counts["providers_payer"] += _run(cur, conn, mix_sql, (bucket, bucket))
            _run(cur, conn, top_sql, (bucket, bucket))
            print(f"phase4 payer overlay bucket {bucket}", flush=True)

        org_sql = f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN {claims}.physician_primary_affiliation a
                ON a.rendering_physician_code = p.npi
            SET
                p.primary_organization_npi = CASE
                    WHEN a.p_provider_billing_code NOT IN ({dummy})
                        THEN a.p_provider_billing_code
                    WHEN a.i_provider_billing_code NOT IN ({dummy})
                        THEN a.i_provider_billing_code
                    ELSE NULL
                END,
                p.primary_organization_id = CASE
                    WHEN a.p_provider_billing_code NOT IN ({dummy})
                        THEN a.p_provider_billing_code
                    WHEN a.i_provider_billing_code NOT IN ({dummy})
                        THEN a.i_provider_billing_code
                    ELSE NULL
                END,
                p.primary_organization_name = COALESCE(
                    NULLIF(TRIM(a.p_dba_name), ''),
                    NULLIF(TRIM(a.I_dba_name), ''),
                    CASE
                        WHEN NULLIF(TRIM(a.group_prac_name), '') IS NOT NULL
                         AND a.group_prac_name <> 'Unknown Group Practice'
                        THEN TRIM(a.group_prac_name)
                    END
                ),
                p.primary_organization_parent_id = COALESCE(
                    a.IP_hospital_system_code,
                    a.OP_hospital_system_code
                ),
                p.primary_organization_parent_name = COALESCE(
                    NULLIF(TRIM(a.IP_hospital_system_name), ''),
                    NULLIF(TRIM(a.OP_hospital_system_name), '')
                ),
                p.refreshed_at = NOW()
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, org_sql, (bucket,))
            counts["providers_org"] += n
            print(f"phase4 org bucket {bucket}: {n} rows", flush=True)

        if has_practice:
            work_sql = f"""
                UPDATE {mart}.pd_provider_practice pr
                INNER JOIN {claims}.sl sl ON sl.sl_code = pr.sl_code
                SET pr.work_type = {work_type_case_sql("sl")}
                WHERE MOD(pr.npi, {PROVIDER_BUCKETS}) = %s
            """
            for bucket in range(PROVIDER_BUCKETS):
                n = _run(cur, conn, work_sql, (bucket,))
                counts["practices_work_type"] += n
                print(f"phase4 work_type bucket {bucket}: {n} rows", flush=True)

            name_sql = f"""
                UPDATE {mart}.pd_provider_practice pr
                SET pr.name = TRIM(BOTH ' ,' FROM CONCAT_WS(
                    ', ',
                    NULLIF(TRIM(pr.street), ''),
                    NULLIF(TRIM(pr.city), '')
                ))
                WHERE MOD(pr.npi, {PROVIDER_BUCKETS}) = %s
                  AND IFNULL(pr.npi_type, '') = '1'
                  AND pr.name IS NOT NULL
                  AND {is_person_practice_name_sql("pr.name")}
                  AND (
                      NULLIF(TRIM(pr.street), '') IS NOT NULL
                      OR NULLIF(TRIM(pr.city), '') IS NOT NULL
                  )
            """
            for bucket in range(PROVIDER_BUCKETS):
                n = _run(cur, conn, name_sql, (bucket,))
                counts["practices_names"] += n
                print(f"phase4 practice name bucket {bucket}: {n} rows", flush=True)

    return {
        "window_start": window_start,
        "window_end": window_end,
        **counts,
    }
