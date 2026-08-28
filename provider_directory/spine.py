"""Type 1 provider spine from az.physician + azal.npi_spec_grp."""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.schema import create_schema
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_DB,
    NPI_MAX,
    NPI_MIN,
    TYPE1_CODE,
)


def spine_select_sql(
    *,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
) -> str:
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    return f"""
        SELECT
            p.physician_code AS npi,
            NULLIF(TRIM(p.first_name), '') AS first_name,
            NULLIF(TRIM(p.middle_name), '') AS middle_name,
            NULLIF(TRIM(p.last_name), '') AS last_name,
            NULLIF(TRIM(p.suffix), '') AS suffix,
            NULLIF(TRIM(p.degree_abbr), '') AS credential,
            p.npi_spec_grp_code AS primary_specialty_code,
            COALESCE(NULLIF(TRIM(s.npi_spec_grp_name), ''), NULLIF(TRIM(p.npi_spec_grp_name), ''))
                AS primary_specialty_description,
            COALESCE(NULLIF(TRIM(s.taxonomy_cat_name), ''), NULLIF(TRIM(p.taxonomy_cat_name), ''))
                AS specialty_classification
        FROM {claims}.physician p
        LEFT JOIN {lookup}.npi_spec_grp s
            ON s.npi_spec_grp_code = p.npi_spec_grp_code
        WHERE p.npi_type = '{TYPE1_CODE}'
          AND p.physician_code NOT IN ({dummy})
          AND (p.state_abbr IS NULL OR p.state_abbr <> 'XX')
          AND p.physician_code BETWEEN {NPI_MIN} AND {NPI_MAX}
    """


def rebuild_spine(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
) -> int:
    create_schema(conn, mart_db)
    mart = quote_ident(mart_db)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {mart}.pd_provider")
        cur.execute(
            f"""
            INSERT INTO {mart}.pd_provider (
                npi, first_name, middle_name, last_name, suffix, credential,
                primary_specialty_code, primary_specialty_description, specialty_classification,
                in_system_provider, name_source, refreshed_at
            )
            SELECT
                src.npi,
                src.first_name,
                src.middle_name,
                src.last_name,
                src.suffix,
                src.credential,
                src.primary_specialty_code,
                src.primary_specialty_description,
                src.specialty_classification,
                NULL,
                'claims',
                NOW()
            FROM ({spine_select_sql(claims_db=claims_db, lookup_db=lookup_db)}) src
            """
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted
