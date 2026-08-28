"""Overlay CMS identity onto pd_provider and fill pd_npi_xwalk."""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.settings import GRAD_AGE_OFFSET, MARKET_STATE, MART_DB, MAX_ESTIMATED_AGE, MIN_GRAD_YEAR, REPORT_YEAR


def overlay_cms(
    conn,
    *,
    mart_db: str = MART_DB,
    report_year: int = REPORT_YEAR,
    market_state: str = MARKET_STATE,
) -> None:
    mart = quote_ident(mart_db)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {mart}.pd_npi_xwalk")
        cur.execute(
            f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN (
                SELECT *
                FROM (
                    SELECT
                        npi,
                        first_name,
                        middle_name,
                        last_name,
                        suffix,
                        credential,
                        gender,
                        med_sch,
                        grd_yr,
                        ind_pac_id,
                        org_pac_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY npi
                            ORDER BY
                                (UPPER(IFNULL(state, '')) = %s) DESC,
                                (med_sch IS NOT NULL AND TRIM(med_sch) <> '' AND UPPER(med_sch) <> 'OTHER') DESC,
                                (grd_yr IS NOT NULL) DESC,
                                (gender IN ('M', 'F')) DESC,
                                (phone IS NOT NULL AND TRIM(phone) <> '') DESC
                        ) AS rn
                    FROM {mart}.cms_pdc_clinician
                ) ranked
                WHERE rn = 1
            ) c ON c.npi = p.npi
            SET
                p.name_source = IF(
                    NULLIF(TRIM(p.last_name), '') IS NULL AND NULLIF(TRIM(c.last_name), '') IS NOT NULL,
                    'pdc',
                    p.name_source
                ),
                p.gender_source = IF(p.gender IS NULL AND c.gender IS NOT NULL, 'pdc', p.gender_source),
                p.school_source = IF(
                    p.medical_school_name IS NULL AND c.med_sch IS NOT NULL AND TRIM(c.med_sch) <> '',
                    'pdc',
                    p.school_source
                ),
                p.first_name = COALESCE(NULLIF(TRIM(p.first_name), ''), NULLIF(TRIM(c.first_name), '')),
                p.middle_name = COALESCE(NULLIF(TRIM(p.middle_name), ''), NULLIF(TRIM(c.middle_name), '')),
                p.last_name = COALESCE(NULLIF(TRIM(p.last_name), ''), NULLIF(TRIM(c.last_name), '')),
                p.suffix = COALESCE(NULLIF(TRIM(p.suffix), ''), NULLIF(TRIM(c.suffix), '')),
                p.credential = COALESCE(NULLIF(TRIM(p.credential), ''), NULLIF(TRIM(c.credential), '')),
                p.gender = COALESCE(p.gender, c.gender),
                p.medical_school_name = COALESCE(
                    NULLIF(TRIM(p.medical_school_name), ''),
                    NULLIF(TRIM(c.med_sch), '')
                ),
                p.medical_school_graduation_year = COALESCE(p.medical_school_graduation_year, c.grd_yr)
            """,
            (market_state,),
        )
        cur.execute(
            f"""
            UPDATE {mart}.pd_provider p
            INNER JOIN {mart}.cms_nppes_type1 n ON n.npi = p.npi
            SET
                p.name_source = IF(
                    NULLIF(TRIM(p.last_name), '') IS NULL AND NULLIF(TRIM(n.last_name), '') IS NOT NULL,
                    'nppes',
                    p.name_source
                ),
                p.gender_source = IF(p.gender IS NULL AND n.gender IS NOT NULL, 'nppes', p.gender_source),
                p.first_name = COALESCE(NULLIF(TRIM(p.first_name), ''), NULLIF(TRIM(n.first_name), '')),
                p.middle_name = COALESCE(NULLIF(TRIM(p.middle_name), ''), NULLIF(TRIM(n.middle_name), '')),
                p.last_name = COALESCE(NULLIF(TRIM(p.last_name), ''), NULLIF(TRIM(n.last_name), '')),
                p.suffix = COALESCE(NULLIF(TRIM(p.suffix), ''), NULLIF(TRIM(n.suffix), '')),
                p.credential = COALESCE(NULLIF(TRIM(p.credential), ''), NULLIF(TRIM(n.credential), '')),
                p.gender = COALESCE(p.gender, n.gender)
            """
        )
        cur.execute(
            f"""
            UPDATE {mart}.pd_provider
            SET estimated_age = LEAST(
                %s,
                %s - medical_school_graduation_year + %s
            )
            WHERE medical_school_graduation_year BETWEEN %s AND %s
            """,
            (MAX_ESTIMATED_AGE, report_year, GRAD_AGE_OFFSET, MIN_GRAD_YEAR, report_year),
        )
        cur.execute(
            f"""
            INSERT INTO {mart}.pd_npi_xwalk (npi, ind_pac_id, org_pac_id, pdc_loaded_at)
            SELECT p.npi, c.ind_pac_id, c.org_pac_id, NOW()
            FROM {mart}.pd_provider p
            LEFT JOIN (
                SELECT npi, ind_pac_id, org_pac_id
                FROM (
                    SELECT
                        npi, ind_pac_id, org_pac_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY npi
                            ORDER BY (UPPER(IFNULL(state, '')) = %s) DESC
                        ) AS rn
                    FROM {mart}.cms_pdc_clinician
                ) ranked
                WHERE rn = 1
            ) c ON c.npi = p.npi
            ON DUPLICATE KEY UPDATE
                ind_pac_id = COALESCE(VALUES(ind_pac_id), {mart}.pd_npi_xwalk.ind_pac_id),
                org_pac_id = COALESCE(VALUES(org_pac_id), {mart}.pd_npi_xwalk.org_pac_id),
                pdc_loaded_at = VALUES(pdc_loaded_at)
            """,
            (market_state,),
        )
        cur.execute(
            f"""
            INSERT INTO {mart}.pd_npi_xwalk (npi, nppes_last_update, nppes_loaded_at)
            SELECT p.npi, n.last_update_date, NOW()
            FROM {mart}.pd_provider p
            INNER JOIN {mart}.cms_nppes_type1 n ON n.npi = p.npi
            ON DUPLICATE KEY UPDATE
                nppes_last_update = COALESCE(VALUES(nppes_last_update), {mart}.pd_npi_xwalk.nppes_last_update),
                nppes_loaded_at = VALUES(nppes_loaded_at)
            """
        )
        cur.execute(f"UPDATE {mart}.pd_provider SET refreshed_at = NOW()")
    conn.commit()
