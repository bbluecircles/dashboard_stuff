"""Phase 3: claims-weighted practice locations. Reads az/az_pd, writes az_pd only.

Uses Phase 2 staging (pd_stg_visit + pd_stg_window_claim). Does not rescan
az.pat_dt and does not drop Phase 2 tables.

Visit grain: one sl_code per encounter (modal claim sl). Cluster suite/geo
duplicates, rank five sites by visit volume, overlay PDC phone onto matched
sites. Never copy an NPPES or PDC street over a claims site.
"""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.schema import create_schema, drop_phase3_staging, drop_staging_tables, table_options
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    MART_COLLATION,
    MART_DB,
    MAX_PRACTICE_SITES,
)

VISIT_BUCKETS = 16
PROVIDER_BUCKETS = 16


class Phase2Required(RuntimeError):
    pass


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


def street_key_sql(expr: str) -> str:
    """Suite-stripped uppercase street for clustering / PDC match."""
    return f"""
        TRIM(REGEXP_REPLACE(
            REGEXP_REPLACE(
                REGEXP_REPLACE(UPPER(IFNULL({expr}, '')), '[,.]', ' '),
                '[[:space:]]+(STE|SUITE|APT|UNIT|BLDG|BUILDING|FLOOR|FL|#)([.[:space:]].*)?$',
                ''
            ),
            '[[:space:]]+',
            ' '
        ))
    """


def cluster_key_sql(street_expr: str, zip_expr: str, lat_expr: str, lon_expr: str, sl_expr: str) -> str:
    street_key = street_key_sql(street_expr)
    zip5 = f"LEFT(TRIM(IFNULL({zip_expr}, '')), 5)"
    return f"""
        LEFT(CASE
            WHEN {street_key} <> '' AND {zip5} <> ''
                THEN CONCAT('a:', {street_key}, '|', {zip5})
            WHEN {lat_expr} IS NOT NULL AND {lon_expr} IS NOT NULL
                 AND NOT ({lat_expr} = 0 AND {lon_expr} = 0)
                THEN CONCAT('g:', ROUND({lat_expr}, 4), ',', ROUND({lon_expr}, 4))
            ELSE CONCAT('s:', IFNULL({sl_expr}, 0))
        END, 180)
    """


def po_box_sql(street_expr: str) -> str:
    # CHAR(37) is '%'. Do not put a literal % here — pymysql treats it as a
    # format placeholder whenever the statement also has %s params.
    return (
        "REPLACE(REPLACE(REPLACE(UPPER(IFNULL("
        f"{street_expr}, '')), '.', ''), ' ', ''), '-', '') LIKE CONCAT('POBOX', CHAR(37))"
    )


def practice_name_sql(sl: str = "sl", fac: str = "fac") -> str:
    return f"""
        TRIM(REGEXP_REPLACE(COALESCE(
            NULLIF(TRIM({sl}.sl_hospital_system_name), ''),
            NULLIF(TRIM({fac}.provider_facility_npi_hospital_system_name), ''),
            CASE WHEN {sl}.npi_type = '2' THEN NULLIF(TRIM({sl}.sl_dba_name), '') END,
            CASE WHEN {sl}.npi_type = '2' THEN NULLIF(TRIM({sl}.sl_name), '') END,
            NULLIF(TRIM({fac}.PROVIDER_FACILITY_NPI_dba_name), ''),
            NULLIF(TRIM({sl}.sl_common_name), ''),
            NULLIF(TRIM({sl}.sl_dba_name), ''),
            NULLIF(TRIM({sl}.sl_name), '')
        ), '[[:space:]]*TYPE-2-ENTITY[[:space:]]*$', ''))
    """


def work_type_sql(sl: str = "sl", fac: str = "fac") -> str:
    return f"""
        CASE
            WHEN NULLIF(TRIM(COALESCE({sl}.sl_hospital_system_name, {fac}.provider_facility_npi_hospital_system_name)), '') IS NOT NULL
                THEN COALESCE(NULLIF(TRIM({sl}.im_specialty_rollup), ''), 'Hospital')
            ELSE COALESCE(NULLIF(TRIM({sl}.pos_type_name), ''), NULLIF(TRIM({sl}.im_specialty_rollup), ''))
        END
    """


def require_phase2_staging(conn, mart_db: str = MART_DB) -> None:
    mart = quote_ident(mart_db)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT TABLE_NAME AS name
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME IN ('pd_stg_visit', 'pd_stg_window_claim')
            """,
            (mart_db,),
        )
        found = {row["name"] for row in cur.fetchall()}
        missing = {"pd_stg_visit", "pd_stg_window_claim"} - found
        if missing:
            raise Phase2Required(
                "Phase 2 staging is missing ("
                + ", ".join(sorted(missing))
                + "). Run phase2 first; Phase 3 does not rescan az.pat_dt."
            )
        cur.execute(f"SELECT 1 AS ok FROM {mart}.pd_stg_visit LIMIT 1")
        if cur.fetchone() is None:
            raise Phase2Required("pd_stg_visit is empty. Run phase2 first.")


def table_has_rows(conn, mart_db: str, table: str) -> bool:
    mart = quote_ident(mart_db)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 AS ok
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """,
            (mart_db, table),
        )
        if cur.fetchone() is None:
            return False
        cur.execute(f"SELECT 1 AS ok FROM {mart}.{quote_ident(table)} LIMIT 1")
        return cur.fetchone() is not None


def rebuild_locations(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    max_sites: int = MAX_PRACTICE_SITES,
) -> dict:
    require_phase2_staging(conn, mart_db)
    keep_visit_site = table_has_rows(conn, mart_db, "pd_stg_visit_site")
    if keep_visit_site:
        drop_staging_tables(conn, mart_db, ("pd_stg_npi_sl",))
    else:
        drop_phase3_staging(conn, mart_db)
    mart = quote_ident(mart_db)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 AS ok
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'pd_provider_practice'
            """,
            (mart_db,),
        )
        if cur.fetchone():
            cur.execute(f"TRUNCATE TABLE {mart}.pd_provider_practice")
            conn.commit()
    create_schema(conn, mart_db)
    claims = quote_ident(claims_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    counts: dict[str, int] = {
        "visit_sites": 0,
        "npi_sl_rows": 0,
        "practice_rows": 0,
        "phones_overlaid": 0,
        "providers_updated": 0,
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
                SET provider_practices_total = 0
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}
                """,
            )

        if keep_visit_site:
            cur.execute(
                """
                SELECT TABLE_ROWS AS n
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'pd_stg_visit_site'
                """,
                (mart_db,),
            )
            counts["visit_sites"] = int((cur.fetchone() or {}).get("n") or 0)
            print(
                f"phase3 visit_site reused (~{counts['visit_sites']} rows)",
                flush=True,
            )
        else:
            visit_site_sql = f"""
            INSERT INTO {mart}.pd_stg_visit_site (encounter_id, rendering_npi, sl_code)
            SELECT picked.encounter_id, picked.rendering_npi, picked.sl_code
            FROM (
                SELECT
                    agg.encounter_id,
                    agg.rendering_npi,
                    agg.sl_code,
                    ROW_NUMBER() OVER (
                        PARTITION BY agg.encounter_id
                        ORDER BY agg.claim_rows DESC, agg.sl_code
                    ) AS rk
                FROM (
                    SELECT
                        v.encounter_id,
                        v.rendering_npi,
                        c.sl_code,
                        COUNT(*) AS claim_rows
                    FROM {mart}.pd_stg_visit v
                    INNER JOIN {mart}.pd_stg_window_claim c
                        ON c.encounter_id = v.encounter_id
                    INNER JOIN {mart}.pd_provider p
                        ON p.npi = v.rendering_npi
                    WHERE MOD(v.encounter_id, {VISIT_BUCKETS}) = %s
                      AND c.sl_code IS NOT NULL
                      AND c.sl_code NOT IN ({dummy})
                    GROUP BY v.encounter_id, v.rendering_npi, c.sl_code
                ) agg
            ) picked
            WHERE picked.rk = 1
            """
            for bucket in range(VISIT_BUCKETS):
                n = _run(cur, conn, visit_site_sql, (bucket,))
                counts["visit_sites"] += n
                print(f"phase3 visit_site bucket {bucket}: {n} rows", flush=True)

        npi_sl_sql = f"""
            INSERT INTO {mart}.pd_stg_npi_sl (
                npi, sl_code, visits, cluster_key, name, street, city, county,
                state, zip, latitude, longitude, work_type, npi_type, needs_geocode
            )
            SELECT
                agg.npi,
                agg.sl_code,
                agg.visits,
                {cluster_key_sql("sl.street", "sl.zip_code", "sl.latitude", "sl.longitude", "sl.sl_code")},
                {practice_name_sql()},
                NULLIF(TRIM(sl.street), ''),
                NULLIF(TRIM(SUBSTRING_INDEX(IFNULL(sl.city, ''), ',', 1)), ''),
                NULLIF(TRIM(SUBSTRING_INDEX(IFNULL(sl.county_name, ''), ',', 1)), ''),
                NULLIF(TRIM(sl.state_abbr), ''),
                LEFT(TRIM(sl.zip_code), 5),
                sl.latitude,
                sl.longitude,
                {work_type_sql()},
                NULLIF(TRIM(sl.npi_type), ''),
                IF(
                    sl.latitude IS NULL OR sl.longitude IS NULL
                    OR (sl.latitude = 0 AND sl.longitude = 0),
                    1, 0
                )
            FROM (
                SELECT rendering_npi AS npi, sl_code, COUNT(*) AS visits
                FROM {mart}.pd_stg_visit_site
                WHERE MOD(rendering_npi, {PROVIDER_BUCKETS}) = %s
                GROUP BY rendering_npi, sl_code
            ) agg
            INNER JOIN {claims}.sl sl ON sl.sl_code = agg.sl_code
            LEFT JOIN {claims}.provider_facility_npi fac
                ON fac.PROVIDER_FACILITY_NPI_code = sl.sl_code
            WHERE IFNULL(sl.state_abbr, '') <> 'XX'
              AND NOT ({po_box_sql("sl.street")})
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, npi_sl_sql, (bucket,))
            counts["npi_sl_rows"] += n
            print(f"phase3 npi_sl bucket {bucket}: {n} rows", flush=True)

        practice_sql = f"""
            INSERT INTO {mart}.pd_provider_practice (
                npi, site_rank, sl_code, cluster_key, name, street, city, county,
                state, zip, latitude, longitude, phone, work_type, visits_at_site,
                visit_share_pct, npi_type, location_source, location_flag,
                phone_source, needs_geocode, refreshed_at
            )
            SELECT
                ranked.npi,
                ranked.site_rank,
                canon.sl_code,
                ranked.cluster_key,
                canon.name,
                canon.street,
                canon.city,
                canon.county,
                canon.state,
                canon.zip,
                canon.latitude,
                canon.longitude,
                NULL,
                canon.work_type,
                ranked.visits_at_site,
                ROUND(100.0 * ranked.visits_at_site / NULLIF(p.visits_total, 0), 2),
                canon.npi_type,
                'claims_sl',
                'claims_confirmed',
                NULL,
                canon.needs_geocode,
                NOW()
            FROM (
                SELECT
                    npi,
                    cluster_key,
                    SUM(visits) AS visits_at_site,
                    ROW_NUMBER() OVER (
                        PARTITION BY npi
                        ORDER BY SUM(visits) DESC, MIN(sl_code)
                    ) AS site_rank
                FROM {mart}.pd_stg_npi_sl
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
                GROUP BY npi, cluster_key
            ) ranked
            INNER JOIN (
                SELECT * FROM (
                    SELECT
                        s.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY s.npi, s.cluster_key
                            ORDER BY s.visits DESC, s.sl_code
                        ) AS rn
                    FROM {mart}.pd_stg_npi_sl s
                    WHERE MOD(s.npi, {PROVIDER_BUCKETS}) = %s
                ) pick
                WHERE pick.rn = 1
            ) canon
                ON canon.npi = ranked.npi
               AND canon.cluster_key = ranked.cluster_key
            INNER JOIN {mart}.pd_provider p ON p.npi = ranked.npi
            WHERE ranked.site_rank <= {int(max_sites)}
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, practice_sql, (bucket, bucket))
            counts["practice_rows"] += n
            print(f"phase3 rank bucket {bucket}: {n} rows", flush=True)

        pdc_street = street_key_sql("c.adr_ln_1")
        site_street = street_key_sql("pr2.street")
        cur.execute(
            f"""
            CREATE TEMPORARY TABLE tmp_pdc_phone (
                npi BIGINT UNSIGNED NOT NULL,
                site_rank TINYINT UNSIGNED NOT NULL,
                phone VARCHAR(20) NOT NULL,
                PRIMARY KEY (npi, site_rank)
            ) {table_options()}
            """
        )
        conn.commit()
        phone_insert_sql = f"""
            INSERT INTO tmp_pdc_phone (npi, site_rank, phone)
            SELECT matched.npi, matched.site_rank, matched.phone
            FROM (
                SELECT
                    pr2.npi,
                    pr2.site_rank,
                    NULLIF(TRIM(c.phone), '') AS phone,
                    ROW_NUMBER() OVER (
                        PARTITION BY pr2.npi, pr2.site_rank
                        ORDER BY
                            ({pdc_street} <> '' AND {pdc_street} = {site_street}) DESC,
                            (
                                UPPER(TRIM(IFNULL(c.city, ''))) COLLATE {MART_COLLATION}
                                = UPPER(IFNULL(pr2.city, '')) COLLATE {MART_COLLATION}
                            ) DESC,
                            (NULLIF(TRIM(c.phone), '') IS NOT NULL) DESC,
                            c.adrs_id
                    ) AS rk
                FROM {mart}.pd_provider_practice pr2
                INNER JOIN {mart}.cms_pdc_clinician c ON c.npi = pr2.npi
                WHERE MOD(pr2.npi, {PROVIDER_BUCKETS}) = %s
                  AND LEFT(TRIM(IFNULL(c.zip, '')), 5) = pr2.zip
                  AND (
                        ({pdc_street} <> '' AND {pdc_street} = {site_street})
                     OR (
                            pr2.city IS NOT NULL AND pr2.city <> ''
                            AND UPPER(TRIM(IFNULL(c.city, ''))) COLLATE {MART_COLLATION}
                                = UPPER(pr2.city) COLLATE {MART_COLLATION}
                        )
                  )
            ) matched
            WHERE matched.rk = 1 AND matched.phone IS NOT NULL
        """
        phone_update_sql = f"""
            UPDATE {mart}.pd_provider_practice pr
            INNER JOIN tmp_pdc_phone src
                ON src.npi = pr.npi AND src.site_rank = pr.site_rank
            SET pr.phone = src.phone,
                pr.phone_source = 'pdc'
        """
        for bucket in range(PROVIDER_BUCKETS):
            _run(cur, conn, "TRUNCATE TABLE tmp_pdc_phone")
            _run(cur, conn, phone_insert_sql, (bucket,))
            n = _run(cur, conn, phone_update_sql)
            counts["phones_overlaid"] += n
            print(f"phase3 phone bucket {bucket}: {n} rows", flush=True)

        totals_sql = f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN (
                SELECT npi, COUNT(DISTINCT cluster_key) AS n
                FROM {mart}.pd_stg_npi_sl
                WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
                GROUP BY npi
            ) t ON t.npi = p.npi
            SET p.provider_practices_total = t.n,
                p.refreshed_at = NOW()
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, totals_sql, (bucket, bucket))
            counts["providers_updated"] += n
            print(f"phase3 totals bucket {bucket}: {n} rows", flush=True)

    return {"max_sites": max_sites, **counts}
