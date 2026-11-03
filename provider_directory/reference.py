"""Reference server (master_ref): copy 111_physician and cms_group_practice into
the mart, overlay the single affiliation onto pd_provider, and report how the
reference groups line up with the warehouse groups.

The reference tables live on a different MariaDB host and are shared with
another team, so this module never joins across servers and never locks the
source: every read is a short keyset-paged SELECT on a read-only session
(db.get_ref_connection: autocommit, READ UNCOMMITTED, max_statement_time).
Writes go to pd_src_* tables in the mart only, built as *_new and swapped in
with one RENAME so readers never see a half-copied table.
"""

from __future__ import annotations

from typing import Iterable

from provider_directory.db import get_ref_connection, quote_ident, ref_db_config
from provider_directory.schema import PD_PROVIDER_REFERENCE_COLUMNS, create_schema
from provider_directory.settings import (
    MARKET_STATE,
    MART_DB,
    REF_COPY_PAGE_ROWS,
    REF_DB,
    REF_GROUP_PRACTICE_TABLE,
    REF_PHYSICIAN_TABLE,
)
from provider_directory.transforms import ORG_NAME_REGEXP, PERSON_NAME_REGEXP, clean_name_sql

PROVIDER_BUCKETS = 16
SPINE_LOOKUP_BATCH = 1000

# (mart column, source column) for pd_src_physician. Source names are quoted
# verbatim because the reference table mixes cases.
PHYSICIAN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("npi", "physician_code"),
    ("npi_type", "npi_type"),
    ("first_name", "first_name"),
    ("middle_name", "middle_name"),
    ("last_name", "last_name"),
    ("suffix", "suffix"),
    ("specialty_primary", "specialty_primary"),
    ("specialty_grp_code", "specialty_grp_code"),
    ("specialty_grp_abbr", "specialty_grp_abbr"),
    ("specialty_grp_name", "specialty_grp_name"),
    ("npi_spec_grp_code", "npi_spec_grp_code"),
    ("npi_spec_grp_name", "npi_spec_grp_name"),
    ("taxonomy_cat", "taxonomy_cat"),
    ("taxonomy_cat_name", "taxonomy_cat_name"),
    ("degree_code", "degree_code"),
    ("degree_abbr", "degree_abbr"),
    ("degree_name", "degree_name"),
    ("zip_code", "zip_code"),
    ("street", "street"),
    ("city", "city"),
    ("county_code", "county_code"),
    ("county_name", "county_name"),
    ("state_abbr", "state_abbr"),
    ("physician_name", "physician_name"),
    ("dba_name", "dba_name"),
    ("latitude", "latitude"),
    ("longitude", "longitude"),
    ("phone_number", "phone_number"),
    ("group_prac_code", "group_prac_code"),
    ("group_prac_name", "group_prac_name"),
    ("inpatient", "inpatient"),
    ("outpatient", "outpatient"),
    ("ed", "ed"),
    ("claim", "claim"),
    ("affiliation_code", "affiliation_code"),
    ("affiliation_name", "affiliation_name"),
    ("affiliation_dba_name", "affiliation_dba_name"),
    ("rec_create_date", "rec_create_date"),
    ("rec_modify_date", "rec_modify_date"),
)

# (mart column, source column) for pd_src_group_practice. The CMS file's
# column names contain spaces; the mart copy uses plain identifiers.
GROUP_PRACTICE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("rec_id", "rec_id"),
    ("group_pac_id", "Group PAC ID"),
    ("group_enrollment_id", "Group Enrollment ID"),
    ("group_name", "Group Legal Business Name"),
    ("group_state", "Group State Code"),
    ("group_member_count", "Group Reassignments and Physician Assistants"),
    ("record_type", "Record Type"),
    ("individual_enrollment_id", "Individual Enrollment ID"),
    ("npi", "Individual NPI"),
    ("first_name", "Individual First Name"),
    ("last_name", "Individual Last Name"),
    ("individual_state", "Individual State Code"),
    ("specialty_description", "Individual Specialty Description"),
    ("employer_associations", "Individual Total Employer Associations"),
)


def _q(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _src(table: str) -> str:
    return f"{_q(REF_DB)}.{_q(table)}"


def physician_page_sql(table: str = REF_PHYSICIAN_TABLE) -> str:
    """One page of reference physicians for a state, keyset-paged on the primary
    key. Params: state, last physician_code seen, page size. Read-only."""
    cols = ", ".join(_q(src) for _dst, src in PHYSICIAN_COLUMNS)
    return (
        f"SELECT {cols} FROM {_src(table)} "
        "WHERE state_abbr = %s AND physician_code > %s "
        "ORDER BY physician_code LIMIT %s"
    )


def physician_lookup_sql(npis: Iterable[int], table: str = REF_PHYSICIAN_TABLE) -> str:
    """Primary-key lookups for spine NPIs whose reference address is out of state."""
    cols = ", ".join(_q(src) for _dst, src in PHYSICIAN_COLUMNS)
    listed = ", ".join(str(int(n)) for n in npis)
    return f"SELECT {cols} FROM {_src(table)} WHERE physician_code IN ({listed})"


def group_practice_page_sql(table: str = REF_GROUP_PRACTICE_TABLE) -> str:
    """One page of CMS group membership rows touching a state, keyset-paged on
    rec_id. Params: state, state, last rec_id seen, page size. Read-only."""
    cols = ", ".join(_q(src) for _dst, src in GROUP_PRACTICE_COLUMNS)
    return (
        f"SELECT {cols} FROM {_src(table)} "
        "WHERE (`Group State Code` = %s OR `Individual State Code` = %s) AND rec_id > %s "
        "ORDER BY rec_id LIMIT %s"
    )


def _insert_sql(mart: str, table: str, columns: tuple[tuple[str, str], ...]) -> str:
    names = ", ".join(dst for dst, _src in columns)
    marks = ", ".join(["%s"] * len(columns))
    return f"INSERT IGNORE INTO {mart}.{table} ({names}) VALUES ({marks})"


def _rows_as_tuples(rows: list[dict], columns: tuple[tuple[str, str], ...]) -> list[tuple]:
    return [tuple(row.get(src) for _dst, src in columns) for row in rows]


def _swap_in(cur, conn, mart: str, table: str) -> None:
    """RENAME {table}_new over {table} in one statement; drop the old copy after."""
    cur.execute(f"DROP TABLE IF EXISTS {mart}.{table}_old")
    cur.execute(
        f"""
        SELECT COUNT(*) AS n FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (mart.strip("`"), table),
    )
    exists = int(cur.fetchone()["n"]) > 0
    if exists:
        cur.execute(f"RENAME TABLE {mart}.{table} TO {mart}.{table}_old, {mart}.{table}_new TO {mart}.{table}")
        cur.execute(f"DROP TABLE IF EXISTS {mart}.{table}_old")
    else:
        cur.execute(f"RENAME TABLE {mart}.{table}_new TO {mart}.{table}")
    conn.commit()


def _create_new_like(cur, conn, mart: str, table: str) -> None:
    cur.execute(f"DROP TABLE IF EXISTS {mart}.{table}_new")
    cur.execute(f"CREATE TABLE {mart}.{table}_new LIKE {mart}.{table}")
    conn.commit()


def copy_physicians(
    conn,
    ref_conn,
    *,
    mart_db: str = MART_DB,
    state: str = MARKET_STATE,
    page_rows: int = REF_COPY_PAGE_ROWS,
    table: str = REF_PHYSICIAN_TABLE,
) -> dict[str, int]:
    """Copy every 111_physician row with state_abbr = state (all NPI types, so
    organizations are named too), then every spine NPI not covered by the state
    pass, into pd_src_physician. Swapped in atomically at the end."""
    mart = quote_ident(mart_db)
    counts = {"state_rows": 0, "spine_lookup_rows": 0, "pages": 0}
    insert = _insert_sql(mart, "pd_src_physician", PHYSICIAN_COLUMNS)
    with conn.cursor() as cur:
        _create_new_like(cur, conn, mart, "pd_src_physician")
        insert_new = insert.replace("pd_src_physician", "pd_src_physician_new")
        last = 0
        with ref_conn.cursor() as rcur:
            while True:
                rcur.execute(physician_page_sql(table), (state, last, int(page_rows)))
                rows = rcur.fetchall()
                if not rows:
                    break
                cur.executemany(insert_new, _rows_as_tuples(rows, PHYSICIAN_COLUMNS))
                conn.commit()
                counts["state_rows"] += len(rows)
                counts["pages"] += 1
                last = int(rows[-1]["physician_code"])
                print(f"ref physicians {state} page {counts['pages']}: {len(rows)} rows (through {last})", flush=True)
                if len(rows) < int(page_rows):
                    break
            # Spine NPIs whose reference address is in another state.
            cur.execute(
                f"""
                SELECT p.npi FROM {mart}.pd_provider p
                LEFT JOIN {mart}.pd_src_physician_new s ON s.npi = p.npi
                WHERE s.npi IS NULL
                ORDER BY p.npi
                """
            )
            missing = [int(r["npi"]) for r in cur.fetchall()]
            for i in range(0, len(missing), SPINE_LOOKUP_BATCH):
                batch = missing[i : i + SPINE_LOOKUP_BATCH]
                rcur.execute(physician_lookup_sql(batch, table))
                rows = rcur.fetchall()
                if rows:
                    cur.executemany(insert_new, _rows_as_tuples(rows, PHYSICIAN_COLUMNS))
                    conn.commit()
                    counts["spine_lookup_rows"] += len(rows)
            if missing:
                print(f"ref physicians spine lookups: {counts['spine_lookup_rows']} of {len(missing)} found", flush=True)
        cur.execute(f"UPDATE {mart}.pd_src_physician_new SET copied_at = NOW()")
        conn.commit()
        _swap_in(cur, conn, mart, "pd_src_physician")
    return counts


def copy_group_practices(
    conn,
    ref_conn,
    *,
    mart_db: str = MART_DB,
    state: str = MARKET_STATE,
    page_rows: int = REF_COPY_PAGE_ROWS,
    table: str = REF_GROUP_PRACTICE_TABLE,
) -> dict[str, int]:
    """Copy CMS group membership rows where the group or the clinician is in the
    state into pd_src_group_practice. Swapped in atomically at the end."""
    mart = quote_ident(mart_db)
    counts = {"rows": 0, "pages": 0}
    insert = _insert_sql(mart, "pd_src_group_practice_new", GROUP_PRACTICE_COLUMNS)
    with conn.cursor() as cur:
        _create_new_like(cur, conn, mart, "pd_src_group_practice")
        last = 0
        with ref_conn.cursor() as rcur:
            while True:
                rcur.execute(group_practice_page_sql(table), (state, state, last, int(page_rows)))
                rows = rcur.fetchall()
                if not rows:
                    break
                cur.executemany(insert, _rows_as_tuples(rows, GROUP_PRACTICE_COLUMNS))
                conn.commit()
                counts["rows"] += len(rows)
                counts["pages"] += 1
                last = int(rows[-1]["rec_id"])
                print(f"ref group practices {state} page {counts['pages']}: {len(rows)} rows (through {last})", flush=True)
                if len(rows) < int(page_rows):
                    break
        cur.execute(f"UPDATE {mart}.pd_src_group_practice_new SET copied_at = NOW()")
        conn.commit()
        _swap_in(cur, conn, mart, "pd_src_group_practice")
    return counts


def copy_reference_tables(
    conn,
    *,
    mart_db: str = MART_DB,
    state: str = MARKET_STATE,
    page_rows: int = REF_COPY_PAGE_ROWS,
) -> dict:
    """sync --ref step 1: refresh pd_src_physician and pd_src_group_practice."""
    create_schema(conn, mart_db)
    cfg = ref_db_config()
    with get_ref_connection() as ref_conn:
        physicians = copy_physicians(conn, ref_conn, mart_db=mart_db, state=state, page_rows=page_rows)
        groups = copy_group_practices(conn, ref_conn, mart_db=mart_db, state=state, page_rows=page_rows)
    return {
        "reference_host": cfg["host"],
        "reference_db": REF_DB,
        "state": state,
        "physicians": physicians,
        "group_practices": groups,
    }


def self_placeholder_sql(code_expr: str, npi_expr: str) -> str:
    """TRUE when a reference affiliation or group code is a placeholder: the
    clinician's own NPI (solo clinician), 0, or the historical '888' + NPI form."""
    return (
        f"({code_expr} IS NULL OR {code_expr} = 0 OR {code_expr} = {npi_expr} "
        f"OR CAST({code_expr} AS CHAR) = CONCAT('888', CAST({npi_expr} AS CHAR)))"
    )


def affiliation_name_sql(expr: str, npi_expr: str) -> str:
    """Cleaned affiliation / group name: NULL for blank, Unknown, Unknown Group
    Practice, <Unavail>, a 'NAME-NPI' placeholder, or the clinician's own name
    (LAST, FIRST with no organization word)."""
    cleaned = clean_name_sql(expr)
    return f"""
        CASE
            WHEN {cleaned} IS NULL THEN NULL
            WHEN UPPER({cleaned}) IN ('<UNAVAIL>', 'UNAVAIL', 'UNAVAILABLE') THEN NULL
            WHEN {cleaned} REGEXP CONCAT('-', CAST({npi_expr} AS CHAR), '$') THEN NULL
            WHEN UPPER({cleaned}) NOT REGEXP '{ORG_NAME_REGEXP}'
             AND {cleaned} REGEXP '{PERSON_NAME_REGEXP}' THEN NULL
            ELSE LEFT({cleaned}, 180)
        END
    """


def overlay_reference_sql(mart: str, bucket: int) -> str:
    """UPDATE pd_provider from pd_src_physician for one NPI bucket: the single
    affiliation (placeholders cleaned) and the group practice. The master-file
    group (CMS PAC ID) is the group key: it fills primary_organization_id / name
    and is mirrored in reference_group_* for the comparison report. Blank stays
    blank; the columns are always on the profile."""
    code_ok = f"NOT {self_placeholder_sql('s.affiliation_code', 's.npi')}"
    group_ok = f"NOT {self_placeholder_sql('s.group_prac_code', 's.npi')}"
    name = (
        f"COALESCE({affiliation_name_sql('s.affiliation_dba_name', 's.npi')}, "
        f"{affiliation_name_sql('s.affiliation_name', 's.npi')})"
    )
    return f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN {mart}.pd_src_physician s ON s.npi = p.npi
        SET
            p.affiliation_code = CASE WHEN {code_ok} THEN s.affiliation_code END,
            p.affiliation_name = CASE WHEN {code_ok} THEN {name} END,
            p.affiliation_source = CASE WHEN {code_ok} AND {name} IS NOT NULL THEN 'master_ref' END,
            p.reference_group_id = CASE WHEN {group_ok} THEN s.group_prac_code END,
            p.reference_group_name = CASE WHEN {group_ok}
                THEN {affiliation_name_sql('s.group_prac_name', 's.npi')} END,
            p.primary_organization_id = CASE WHEN {group_ok} THEN s.group_prac_code END,
            p.primary_organization_name = CASE WHEN {group_ok}
                THEN {affiliation_name_sql('s.group_prac_name', 's.npi')} END,
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = {int(bucket)}
    """


def claims_group_tiebreak_sql(mart: str, bucket: int) -> str:
    """When the CMS reassignment file lists more than one group for a clinician
    and one of them matches the claims billing organization by name, make that
    group the primary (the master file's single group can be years stale).
    Match: the first 8 letters/digits of the billing name appear in the group name."""
    key = _norm("p.billing_organization_name")
    return f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN (
            SELECT x.npi, x.group_pac_id, x.group_name
            FROM (
                SELECT g.npi,
                       CAST(g.group_pac_id AS UNSIGNED) AS group_pac_id,
                       MAX(g.group_name) AS group_name,
                       ROW_NUMBER() OVER (PARTITION BY g.npi ORDER BY COUNT(*) DESC, g.group_pac_id) AS rk
                FROM {mart}.pd_src_group_practice g
                INNER JOIN {mart}.pd_provider pp ON pp.npi = g.npi
                WHERE MOD(g.npi, {PROVIDER_BUCKETS}) = {int(bucket)}
                  AND g.group_pac_id REGEXP '^[0-9]+$'
                  AND NULLIF(TRIM(pp.billing_organization_name), '') IS NOT NULL
                  AND CHAR_LENGTH({_norm("pp.billing_organization_name")}) >= 5
                  AND {_norm("g.group_name")} LIKE CONCAT('%%', LEFT({_norm("pp.billing_organization_name")}, 8), '%%')
                GROUP BY g.npi, CAST(g.group_pac_id AS UNSIGNED)
            ) x
            WHERE x.rk = 1
        ) m ON m.npi = p.npi
        SET
            p.primary_organization_id = m.group_pac_id,
            p.primary_organization_name = LEFT(TRIM(m.group_name), 180),
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = {int(bucket)}
          AND (p.primary_organization_id IS NULL OR p.primary_organization_id <> m.group_pac_id)
    """


def overlay_reference(conn, *, mart_db: str = MART_DB) -> dict[str, int]:
    """sync --ref step 2: write affiliation_*, reference_group_* and the group key
    (primary_organization_id / name) onto pd_provider. billing_organization_* is
    Phase 4's and is left alone."""
    create_schema(conn, mart_db)
    mart = quote_ident(mart_db)
    null_sql = ", ".join(
        f"{name} = NULL"
        for name, _d in PD_PROVIDER_REFERENCE_COLUMNS
        if not name.startswith("billing_organization")
    ) + ", primary_organization_id = NULL, primary_organization_name = NULL"
    updated = 0
    with conn.cursor() as cur:
        for bucket in range(PROVIDER_BUCKETS):
            cur.execute(f"UPDATE {mart}.pd_provider SET {null_sql} WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}")
            conn.commit()
            cur.execute(overlay_reference_sql(mart, bucket))
            n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            conn.commit()
            updated += n
            print(f"ref overlay bucket {bucket}: {n} rows", flush=True)
        tiebreaks = 0
        if _has_rows(cur, mart, "pd_src_group_practice"):
            for bucket in range(PROVIDER_BUCKETS):
                cur.execute(claims_group_tiebreak_sql(mart, bucket))
                n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                conn.commit()
                tiebreaks += n
                print(f"ref group tie-break bucket {bucket}: {n} rows", flush=True)
    return {"providers_updated": updated, "claims_tiebreaks": tiebreaks}


def _has_rows(cur, mart: str, table: str) -> bool:
    cur.execute(f"SELECT 1 AS ok FROM {mart}.{table} LIMIT 1")
    return cur.fetchone() is not None


def _norm(expr: str) -> str:
    """Organization name key: upper case, letters and digits only, so 'MAYO CLINIC'
    and 'Mayo Clinic' compare equal while different organizations stay apart."""
    return f"REGEXP_REPLACE(UPPER(IFNULL({expr}, '')), '[^A-Z0-9]', '')"


def reference_compare(conn, *, mart_db: str = MART_DB, limit: int = 15) -> dict:
    """ref-compare: how the warehouse groups and affiliations line up with the
    reference file, for the group-key decision. Reads the mart only."""
    mart = quote_ident(mart_db)
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                COUNT(*) AS spine,
                SUM(s.npi IS NOT NULL) AS in_reference,
                SUM(p.affiliation_name IS NOT NULL) AS with_affiliation,
                SUM(p.reference_group_id IS NOT NULL) AS with_reference_group,
                SUM(p.billing_organization_id IS NOT NULL) AS with_warehouse_group,
                SUM(p.billing_organization_id IS NOT NULL AND p.reference_group_id IS NOT NULL
                    AND {_norm('p.billing_organization_name')} = {_norm('p.reference_group_name')}) AS groups_agree_by_name,
                SUM(p.billing_organization_id IS NOT NULL AND p.reference_group_id IS NOT NULL
                    AND {_norm('p.billing_organization_name')} <> {_norm('p.reference_group_name')}) AS groups_differ_by_name,
                SUM(p.reference_group_id IS NOT NULL AND cg.group_pac_id IS NOT NULL) AS reference_group_in_cms_file,
                SUM(p.billing_organization_id IS NOT NULL AND p.reference_group_id IS NULL) AS warehouse_only,
                SUM(p.billing_organization_id IS NULL AND p.reference_group_id IS NOT NULL) AS reference_only,
                SUM(g.npi IS NOT NULL) AS in_cms_group_file
            FROM {mart}.pd_provider p
            LEFT JOIN {mart}.pd_src_physician s ON s.npi = p.npi
            LEFT JOIN (SELECT DISTINCT npi FROM {mart}.pd_src_group_practice) g ON g.npi = p.npi
            LEFT JOIN (SELECT DISTINCT group_pac_id FROM {mart}.pd_src_group_practice) cg
                ON cg.group_pac_id = CAST(p.reference_group_id AS CHAR)
            """
        )
        out["providers"] = cur.fetchone()
        cur.execute(
            f"""
            SELECT p.billing_organization_name AS warehouse_group,
                   p.reference_group_name AS reference_group,
                   COUNT(*) AS providers
            FROM {mart}.pd_provider p
            WHERE p.billing_organization_id IS NOT NULL AND p.reference_group_id IS NOT NULL
              AND {_norm('p.billing_organization_name')} <> {_norm('p.reference_group_name')}
            GROUP BY 1, 2 ORDER BY providers DESC LIMIT %s
            """,
            (int(limit),),
        )
        out["top_group_disagreements"] = cur.fetchall()
        cur.execute(
            f"""
            SELECT p.affiliation_name, COUNT(*) AS providers
            FROM {mart}.pd_provider p
            WHERE p.affiliation_name IS NOT NULL
            GROUP BY 1 ORDER BY providers DESC LIMIT %s
            """,
            (int(limit),),
        )
        out["top_affiliations"] = cur.fetchall()
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT group_pac_id) AS groups,
                   COUNT(DISTINCT npi) AS clinicians,
                   COUNT(*) AS memberships,
                   ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT npi), 0), 2) AS memberships_per_clinician
            FROM {mart}.pd_src_group_practice
            """
        )
        out["cms_group_file"] = cur.fetchone()
        cur.execute(
            f"""
            SELECT state_abbr, npi_type, COUNT(*) AS rows_
            FROM {mart}.pd_src_physician GROUP BY 1, 2 ORDER BY rows_ DESC LIMIT %s
            """,
            (int(limit),),
        )
        out["reference_physician_rows"] = cur.fetchall()
    return out
