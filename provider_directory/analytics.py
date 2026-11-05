"""Phase 4: work RVU per billed line, payer mix, Bills under and the reference group key, group list, hidden affiliation list, work_type polish.

Reads az / azal / existing Phase 2–3 staging. Writes az_pd only.
Does not rescan az.pat_dt or drop Phase 2/3 tables.

Work RVU prices every billed pat_dt line (work_procd_code, else procd_code)
with the strict physician work RVU from the fee schedule (WORK_RVU when it is
at least 0.05, else total − PE − MP, non-facility then facility); the lines
are cached in pd_stg_npi_px_line and wrvu_average is total / priced lines.
Do not round per encounter bucket — Galera splits visits across 16 hashes
and ROUND(0.001, 2) zeros them out. Payer mix reuses az.dash_physician_payor_all claim
counts in the frozen window, except on markets whose payer_source is
"pat_dt" (the az_cms blend, whose summary table only holds native claims):
there it is visit-weighted from pat_dt × the payor lookup, one scan per
month and encounter bucket like Phase 5. Classes 1, 2, 3, 4, 6 and 7 become
the six percents; class 5 Other is left out. Top 5 payers are commercial
parents only. The group key (primary_organization_id, the CMS group PAC ID)
and the affiliation come from the reference copy (reference.overlay_reference);
physician_primary_affiliation only feeds Bills under and the parent.
"""

from __future__ import annotations

from provider_directory.activity import CLAIM_ID_BUCKETS, iter_period_codes
from provider_directory.affiliations import rebuild_org_lists
from provider_directory.db import quote_ident
from provider_directory.group_summary import rebuild_group_summary
from provider_directory.locations import Phase2Required, is_person_practice_name_sql, table_has_rows
from provider_directory.schema import create_schema, drop_phase4_staging
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_COLLATION,
    MART_DB,
    MAX_TOP_PAYERS,
    PAYER_SOURCES,
    PAYOR_COMMERCIAL,
    PAYOR_MIX_CODES,
    PAYOR_MIX_COLUMNS,
    RVU_FILTER_FOLLOWS_PRIMARY_PROCEDURES,
    RVU_LINE_MIN_WORK_RVU,
    WINDOW_END,
    WINDOW_START,
    payer_source_for_claims_db,
)
from provider_directory.transforms import (
    is_unpriced_code_sql,
    strict_work_rvu_sql,
    EMPTY_NAME_SQL_LIST,
    SITE_OFFICE_SUFFIX,
    work_type_case_sql,
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


def rvu_line_filter_sql(provider_alias: str = "p", procd_alias: str = "pr") -> str:
    """Per-clinician work RVU floor (settings.RVU_LINE_MIN_WORK_RVU): TRUE for a
    line that counts toward the clinician's work RVU figures. Lines at or below
    the floor count only when the clinician's primary_procedures_filled flag is
    set (fewer than TOP_N procedures above the floor, so the filter is lifted).
    Always TRUE when RVU_FILTER_FOLLOWS_PRIMARY_PROCEDURES is off."""
    if not RVU_FILTER_FOLLOWS_PRIMARY_PROCEDURES:
        return "1"
    return (
        f"(IFNULL({provider_alias}.primary_procedures_filled, 0) = 1 "
        f"OR {strict_work_rvu_sql(procd_alias)} > {RVU_LINE_MIN_WORK_RVU})"
    )


def wrvu_filter_applied_sql(provider_alias: str = "p") -> str:
    """Value for pd_provider.wrvu_filter_applied under the same rule."""
    if not RVU_FILTER_FOLLOWS_PRIMARY_PROCEDURES:
        return "0"
    return f"CASE WHEN IFNULL({provider_alias}.primary_procedures_filled, 0) = 1 THEN 0 ELSE 1 END"


def priced_line_filter_sql(code_expr: str, procd_alias: str = "pr") -> str:
    """Lines that count toward work RVU: 5-character CPT/HCPCS, not a Category II
    or hospital pass-through C code, with a strict work RVU."""
    return (
        f"{is_hcpcs_sql(code_expr)} AND NOT {is_unpriced_code_sql(code_expr)} "
        f"AND {strict_work_rvu_sql(procd_alias)} IS NOT NULL"
    )


def line_scan_sql(mart: str, claims: str) -> str:
    """One month x pat_id bucket of claim lines into pd_stg_npi_px_line, credited to
    the line's own rendering NPI and location. The priced code is work_procd_code,
    else procd_code. Two %s params: period_code, bucket."""
    code = "COALESCE(NULLIF(TRIM(t.work_procd_code), ''), NULLIF(TRIM(t.procd_code), ''))"
    return f"""
        INSERT INTO {mart}.pd_stg_npi_px_line (npi, sl_code, code, line_count)
        SELECT
            t.rendering_physician_code,
            IFNULL(t.sl_code, 0),
            {code},
            COUNT(*)
        FROM {claims}.pat_dt t
        INNER JOIN {mart}.pd_provider p ON p.npi = t.rendering_physician_code
        WHERE t.period_code = %s
          AND MOD(IFNULL(t.pat_id, 0), {CLAIM_ID_BUCKETS}) = %s
          AND {code} IS NOT NULL
          AND CHAR_LENGTH({code}) = 5
        GROUP BY t.rendering_physician_code, IFNULL(t.sl_code, 0), {code}
        ON DUPLICATE KEY UPDATE
            line_count = {mart}.pd_stg_npi_px_line.line_count + VALUES(line_count)
    """


def _rebuild_rvu(
    cur,
    conn,
    mart: str,
    claims: str,
    lookup: str,
    window_start: int,
    window_end: int,
    counts: dict[str, int],
    *,
    has_practice: bool,
    has_npi_sl: bool,
    rescan_lines: bool = True,
) -> None:
    """Work RVU from the billed procedure lines: scan pat_dt into
    pd_stg_npi_px_line, price each line with the strict rule (Category II codes
    excluded), keep the lines the clinician's floor rule allows
    (rvu_line_filter_sql), sum per NPI and per NPI x location, overlay
    pd_provider and the practice sites. wrvu_average is the average work RVU
    per counted procedure; wrvu_filter_applied says whether the floor was used."""
    for bucket in range(PROVIDER_BUCKETS):
        _run(
            cur,
            conn,
            f"""
            UPDATE {mart}.pd_provider
            SET wrvu_total = NULL, wrvu_average = NULL, wrvu_procedure_count = NULL, wrvu_filter_applied = NULL
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
    for table in ("pd_stg_npi_wrvu", "pd_stg_site_wrvu"):
        _run(cur, conn, f"TRUNCATE TABLE {mart}.{table}")

    if rescan_lines:
        _run(cur, conn, f"TRUNCATE TABLE {mart}.pd_stg_npi_px_line")
        scan = line_scan_sql(mart, claims)
        for period in iter_period_codes(window_start, window_end):
            for bucket in range(CLAIM_ID_BUCKETS):
                n = _run(cur, conn, scan, (period, bucket))
                counts["rvu_line_rows"] += n
                print(f"phase4 rvu lines {period} bucket {bucket}: {n} rows", flush=True)
    else:
        print("phase4 rvu lines reused", flush=True)

    wrvu_expr = strict_work_rvu_sql("pr")
    priced = priced_line_filter_sql("l.code", "pr")
    floor = rvu_line_filter_sql("p", "pr")
    wrvu_sql = f"""
        INSERT INTO {mart}.pd_stg_npi_wrvu (npi, total_wrvu, procedure_count)
        SELECT
            l.npi,
            ROUND(SUM(l.line_count * {wrvu_expr}), 2),
            SUM(l.line_count)
        FROM {mart}.pd_stg_npi_px_line l
        INNER JOIN {mart}.pd_provider p ON p.npi = l.npi
        INNER JOIN {lookup}.procd pr
            ON pr.procd_code = l.code COLLATE {MART_COLLATION}
        WHERE MOD(l.npi, {PROVIDER_BUCKETS}) = %s
          AND {priced}
          AND {floor}
        GROUP BY l.npi
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
            p.wrvu_average = ROUND(w.total_wrvu / NULLIF(w.procedure_count, 0), 3),
            p.wrvu_filter_applied = {wrvu_filter_applied_sql("p")},
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, overlay_wrvu_sql, (bucket,))
        counts["providers_wrvu"] += n
        print(f"phase4 wrvu overlay bucket {bucket}: {n} rows", flush=True)

    site_wrvu_sql = f"""
        INSERT INTO {mart}.pd_stg_site_wrvu (npi, sl_code, total_wrvu, procedure_count)
        SELECT
            l.npi,
            l.sl_code,
            SUM(l.line_count * {wrvu_expr}),
            SUM(l.line_count)
        FROM {mart}.pd_stg_npi_px_line l
        INNER JOIN {mart}.pd_provider p ON p.npi = l.npi
        INNER JOIN {lookup}.procd pr
            ON pr.procd_code = l.code COLLATE {MART_COLLATION}
        WHERE MOD(l.npi, {PROVIDER_BUCKETS}) = %s
          AND l.sl_code <> 0
          AND {priced}
          AND {floor}
        GROUP BY l.npi, l.sl_code
    """
    for bucket in range(PROVIDER_BUCKETS):
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


def rebuild_rvu_only(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
    window_start: int = WINDOW_START,
    window_end: int = WINDOW_END,
    rescan_lines: bool = False,
) -> dict:
    """phase4 --rvu-only: re-price and overlay work RVU. Reuses the staged claim
    lines when pd_stg_npi_px_line has rows (a pricing-rule or fee-schedule change
    then takes minutes); rescan_lines, or an empty staging, scans the 12 months
    again. Payers, organizations and lists are untouched."""
    if not table_has_rows(conn, mart_db, "pd_stg_visit"):
        raise Phase2Required("pd_stg_visit is empty. Run phase2 first.")
    create_schema(conn, mart_db)
    rescan = rescan_lines or not table_has_rows(conn, mart_db, "pd_stg_npi_px_line")
    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    counts: dict[str, int] = {"rvu_line_rows": 0, "wrvu_npi": 0, "providers_wrvu": 0, "site_wrvu_rows": 0, "practices_wrvu": 0}
    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        _rebuild_rvu(
            cur, conn, mart, claims, lookup, window_start, window_end, counts,
            has_practice=table_has_rows(conn, mart_db, "pd_provider_practice"),
            has_npi_sl=table_has_rows(conn, mart_db, "pd_stg_npi_sl"),
            rescan_lines=rescan,
        )
    group_summary = rebuild_group_summary(conn, mart_db)
    return {"rvu_only": True, "lines_rescanned": rescan, "window_start": window_start, "window_end": window_end, **counts, "group_summary": group_summary}


def _payer_null_sql() -> str:
    cols = [column for _code, column in PAYOR_MIX_COLUMNS]
    for i in range(1, MAX_TOP_PAYERS + 1):
        cols += [f"top_payer_name_{i}", f"top_payer_percent_{i}"]
    return ",\n                    ".join(f"{c} = NULL" for c in cols)


def payer_mix_update_sql(mart: str) -> str:
    """UPDATE pd_provider payer-class shares from pd_stg_npi_payor for one NPI bucket.

    Denominator is every class in PAYOR_MIX_COLUMNS (1-4, 6, 7); class 5 Other
    and unmapped codes are outside it. Two %s params: bucket, bucket.
    """
    mix = ", ".join(str(c) for c in PAYOR_MIX_CODES)
    sums = ",\n                    ".join(
        f"SUM(CASE WHEN is_payor_code = {int(code)} THEN claim_count ELSE 0 END) AS c{int(code)}"
        for code, _column in PAYOR_MIX_COLUMNS
    )
    sets = ",\n                ".join(
        f"p.{column} = ROUND(100.0 * m.c{int(code)} / NULLIF(m.denom, 0), 2)"
        for code, column in PAYOR_MIX_COLUMNS
    )
    return f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN (
                SELECT
                    npi,
                    {sums},
                    SUM(CASE WHEN is_payor_code IN ({mix}) THEN claim_count ELSE 0 END) AS denom
                FROM {mart}.pd_stg_npi_payor
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
                GROUP BY npi
            ) m ON m.npi = p.npi
            SET
                {sets},
                p.refreshed_at = NOW()
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """


def top_payer_update_sql(mart: str, n: int = MAX_TOP_PAYERS) -> str:
    """UPDATE pd_provider top_payer_name_1..n / top_payer_percent_1..n for one NPI bucket.

    Commercial (class 3) parents only; percents are of commercial claims.
    Two %s params: bucket, bucket.
    """
    picks = ",\n                    ".join(
        f"MAX(CASE WHEN rk = {i} THEN payor_parent_name END) AS n{i},\n"
        f"                    MAX(CASE WHEN rk = {i} THEN pct END) AS p{i}"
        for i in range(1, n + 1)
    )
    sets = ",\n                ".join(
        f"p.top_payer_name_{i} = t.n{i},\n                p.top_payer_percent_{i} = t.p{i}"
        for i in range(1, n + 1)
    )
    return f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN (
                SELECT
                    npi,
                    {picks}
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
                WHERE rk <= {int(n)}
                GROUP BY npi
            ) t ON t.npi = p.npi
            SET
                {sets}
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """


def _apply_payer_columns(cur, conn, mart: str) -> int:
    """Run the mix and top-payer updates bucket by bucket. Returns providers updated."""
    mix_sql = payer_mix_update_sql(mart)
    top_sql = top_payer_update_sql(mart)
    updated = 0
    for bucket in range(PROVIDER_BUCKETS):
        updated += _run(cur, conn, mix_sql, (bucket, bucket))
        _run(cur, conn, top_sql, (bucket, bucket))
        print(f"phase4 payer overlay bucket {bucket}", flush=True)
    return updated


def rebuild_payer_columns(conn, *, mart_db: str = MART_DB) -> dict:
    """Refill the payer-class shares and top commercial payers from the existing
    pd_stg_npi_payor staging, without re-reading claims (phase4 --payers-only).

    Use after the payer-class mapping or column set changes. The staging table
    is left by the last full Phase 4 run and already carries every class code.
    """
    if not table_has_rows(conn, mart_db, "pd_stg_npi_payor"):
        raise Phase2Required("pd_stg_npi_payor is empty. Run a full phase4 first.")
    from provider_directory.schema import migrate_phase4_columns

    create_schema(conn, mart_db)
    migrate_phase4_columns(conn, mart_db)
    conn.commit()
    mart = quote_ident(mart_db)
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
                    {_payer_null_sql()}
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}
                """,
            )
        providers_payer = _apply_payer_columns(cur, conn, mart)
    group_summary = rebuild_group_summary(conn, mart_db)
    return {
        "payers_only": True,
        "providers_payer": providers_payer,
        "group_summary": group_summary,
    }


def billing_organization_sql(mart: str, claims: str, dummy: str) -> str:
    """UPDATE pd_provider with the claims billing organization (Bills under), the
    billing NPI and the parent from physician_primary_affiliation. One %s: NPI bucket."""
    return f"""
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
                p.billing_organization_id = CASE
                    WHEN a.p_provider_billing_code NOT IN ({dummy})
                        THEN a.p_provider_billing_code
                    WHEN a.i_provider_billing_code NOT IN ({dummy})
                        THEN a.i_provider_billing_code
                    ELSE NULL
                END,
                p.billing_organization_name = COALESCE(
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


def overlay_billing_organization(conn, *, mart_db: str = MART_DB, claims_db: str = CLAIMS_DB) -> int:
    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    sql = billing_organization_sql(mart, claims, dummy)
    total = 0
    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, sql, (bucket,))
            total += n
            print(f"phase4 org bucket {bucket}: {n} rows", flush=True)
    return total


def rebuild_analytics(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
    window_start: int = WINDOW_START,
    window_end: int = WINDOW_END,
    payer_source: str | None = None,
) -> dict:
    if not table_has_rows(conn, mart_db, "pd_stg_visit"):
        raise Phase2Required("pd_stg_visit is empty. Run phase2 first.")
    if payer_source is None:
        payer_source = payer_source_for_claims_db(claims_db)
    if payer_source not in PAYER_SOURCES:
        raise ValueError(f"payer_source must be one of {PAYER_SOURCES}, got {payer_source!r}")
    drop_phase4_staging(conn, mart_db)
    create_schema(conn, mart_db)

    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    period_in = period_in_sql(window_start, window_end)
    has_visit_site = table_has_rows(conn, mart_db, "pd_stg_visit_site")
    has_npi_sl = table_has_rows(conn, mart_db, "pd_stg_npi_sl")
    has_practice = table_has_rows(conn, mart_db, "pd_provider_practice")
    counts: dict[str, int] = {
        "rvu_line_rows": 0,
        "wrvu_npi": 0,
        "site_wrvu_rows": 0,
        "payor_rows": 0,
        "providers_wrvu": 0,
        "providers_payer": 0,
        "providers_org": 0,
        "practices_wrvu": 0,
        "practices_work_type": 0,
        "practices_names": 0,
        "group_practice_rows": 0,
        "hospital_affiliation_rows": 0,
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
                    {_payer_null_sql()},
                    primary_organization_id = NULL,
                    primary_organization_name = NULL,
                    primary_organization_npi = NULL,
                    primary_organization_parent_id = NULL,
                    primary_organization_parent_name = NULL,
                    billing_organization_id = NULL,
                    billing_organization_name = NULL
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}
                """,
            )
        _rebuild_rvu(
            cur, conn, mart, claims, lookup, window_start, window_end, counts,
            has_practice=has_practice, has_npi_sl=has_npi_sl,
        )

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
        payor_claims_sql = f"""
            INSERT INTO {mart}.pd_stg_npi_payor (
                npi, is_payor_code, payor_parent_name, claim_count
            )
            SELECT
                v.rendering_npi,
                v.is_payor_code,
                v.payor_parent_name,
                COUNT(*)
            FROM (
                SELECT
                    t.encounter_id,
                    MAX(t.encounter_rendering_physician_code) AS rendering_npi,
                    py.is_payor_code,
                    LEFT(COALESCE(
                        NULLIF(TRIM(py.payor_parent_name), ''),
                        NULLIF(TRIM(py.payor_name), ''),
                        'Unknown'
                    ), 140) AS payor_parent_name
                FROM {claims}.pat_dt t
                INNER JOIN {claims}.payor py
                    ON py.payor_code = COALESCE(
                        NULLIF(TRIM(t.encounter_payor_code), ''), t.payor_code
                    ) COLLATE {MART_COLLATION}
                WHERE t.period_code = %s
                  AND MOD(ABS(IFNULL(t.encounter_id, 0)), {VISIT_BUCKETS}) = %s
                  AND t.encounter_id IS NOT NULL AND t.encounter_id <> 0
                  AND py.is_payor_code IS NOT NULL
                GROUP BY t.encounter_id, py.is_payor_code, payor_parent_name
            ) v
            INNER JOIN {mart}.pd_provider p ON p.npi = v.rendering_npi
            WHERE v.rendering_npi NOT IN ({dummy})
            GROUP BY v.rendering_npi, v.is_payor_code, v.payor_parent_name
            ON DUPLICATE KEY UPDATE
                claim_count = {mart}.pd_stg_npi_payor.claim_count + VALUES(claim_count)
        """
        if payer_source == "pat_dt":
            for period in iter_period_codes(window_start, window_end):
                for bucket in range(VISIT_BUCKETS):
                    n = _run(cur, conn, payor_claims_sql, (period, bucket))
                    counts["payor_rows"] += n
                    print(f"phase4 payor {period} bucket {bucket}: {n} rows", flush=True)
        else:
            for bucket in range(PROVIDER_BUCKETS):
                n = _run(cur, conn, payor_sql, (bucket,))
                counts["payor_rows"] += n
                print(f"phase4 payor bucket {bucket}: {n} rows", flush=True)

        counts["providers_payer"] = _apply_payer_columns(cur, conn, mart)

        counts["providers_org"] = overlay_billing_organization(conn, mart_db=mart_db, claims_db=claims_db)

    # Group practice (primary_organization_*) comes from the master-file copy; refill
    # it here so a full Phase 4 never leaves the group pages empty.
    if table_has_rows(conn, mart_db, "pd_src_physician"):
        from provider_directory.reference import overlay_reference

        counts["providers_reference"] = overlay_reference(conn, mart_db=mart_db)["providers_updated"]
    else:
        print("phase4 reference overlay skipped: pd_src_physician is empty (run sync --ref)", flush=True)

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()

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

            # A clinician's own LAST, FIRST as the site name keeps the name with
            # ' Office' appended (same rule as Phase 3's site_name_sql).
            office_sql = f"""
                UPDATE {mart}.pd_provider_practice pr
                SET pr.name = CONCAT(TRIM(pr.name), '{SITE_OFFICE_SUFFIX}')
                WHERE MOD(pr.npi, {PROVIDER_BUCKETS}) = %s
                  AND {is_person_practice_name_sql("pr.name")}
                  AND UPPER(TRIM(pr.name)) NOT LIKE CONCAT('%%', UPPER('{SITE_OFFICE_SUFFIX}'))
            """
            name_sql = f"""
                UPDATE {mart}.pd_provider_practice pr
                SET pr.name = TRIM(BOTH ' ,' FROM CONCAT_WS(
                    ', ',
                    NULLIF(TRIM(pr.street), ''),
                    NULLIF(TRIM(pr.city), '')
                ))
                WHERE MOD(pr.npi, {PROVIDER_BUCKETS}) = %s
                  AND (
                      pr.name IS NULL
                      OR UPPER(TRIM(pr.name)) IN {EMPTY_NAME_SQL_LIST}
                  )
                  AND (
                      NULLIF(TRIM(pr.street), '') IS NOT NULL
                      OR NULLIF(TRIM(pr.city), '') IS NOT NULL
                  )
            """
            for bucket in range(PROVIDER_BUCKETS):
                n = _run(cur, conn, office_sql, (bucket,))
                n += _run(cur, conn, name_sql, (bucket,))
                counts["practices_names"] += n
                print(f"phase4 practice name bucket {bucket}: {n} rows", flush=True)

    org_lists = rebuild_org_lists(
        conn,
        mart_db=mart_db,
        claims_db=claims_db,
        window_start=window_start,
    )
    counts["group_practice_rows"] = org_lists.get("group_practice_rows", 0)
    counts["hospital_affiliation_rows"] = org_lists.get("hospital_affiliation_rows", 0)

    group_summary = rebuild_group_summary(conn, mart_db)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "payer_source": payer_source,
        **counts,
        "group_summary": group_summary,
    }
