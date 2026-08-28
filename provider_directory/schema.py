"""DDL for CMS staging + Phase 1 mart tables."""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.settings import MART_DB, require_ident

TABLES = (
    "cms_pdc_clinician",
    "cms_pdc_facility_affil",
    "cms_nppes_type1",
    "pd_provider",
    "pd_npi_xwalk",
    "pd_network_npi",
)


def ddl_statements(mart_db: str = MART_DB) -> list[str]:
    db = quote_ident(require_ident(mart_db, "mart database"))
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {db}.cms_pdc_clinician (
            npi BIGINT UNSIGNED NOT NULL,
            ind_pac_id VARCHAR(16),
            ind_enrl_id VARCHAR(32),
            last_name VARCHAR(150),
            first_name VARCHAR(150),
            middle_name VARCHAR(150),
            suffix VARCHAR(20),
            gender CHAR(1),
            credential VARCHAR(50),
            med_sch VARCHAR(150),
            grd_yr SMALLINT,
            pri_spec VARCHAR(120),
            telehlth VARCHAR(8),
            org_pac_id VARCHAR(16),
            num_org_mem INT,
            adr_ln_1 VARCHAR(150),
            adr_ln_2 VARCHAR(150),
            city VARCHAR(80),
            state CHAR(2),
            zip VARCHAR(16),
            phone VARCHAR(20),
            adrs_id VARCHAR(48),
            loaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_npi (npi),
            KEY idx_state (state)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.cms_pdc_facility_affil (
            npi BIGINT UNSIGNED NOT NULL,
            ind_pac_id VARCHAR(16),
            last_name VARCHAR(150),
            first_name VARCHAR(150),
            facility_type VARCHAR(80),
            ccn VARCHAR(16),
            facility_type_ccn VARCHAR(16),
            loaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_npi (npi),
            KEY idx_ccn (ccn)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.cms_nppes_type1 (
            npi BIGINT UNSIGNED NOT NULL,
            last_name VARCHAR(150),
            first_name VARCHAR(150),
            middle_name VARCHAR(150),
            suffix VARCHAR(20),
            credential VARCHAR(50),
            gender CHAR(1),
            primary_taxonomy VARCHAR(16),
            practice_state CHAR(2),
            mailing_state CHAR(2),
            last_update_date DATE,
            deactivation_date DATE,
            sole_proprietor CHAR(1),
            loaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_provider (
            npi BIGINT UNSIGNED NOT NULL,
            first_name VARCHAR(150),
            middle_name VARCHAR(150),
            last_name VARCHAR(150),
            suffix VARCHAR(20),
            credential VARCHAR(50),
            gender CHAR(1),
            medical_school_name VARCHAR(150),
            medical_school_graduation_year SMALLINT,
            estimated_age TINYINT UNSIGNED,
            primary_specialty_code VARCHAR(25),
            primary_specialty_description VARCHAR(120),
            specialty_classification VARCHAR(120),
            in_system_provider TINYINT NULL,
            name_source VARCHAR(16),
            gender_source VARCHAR(16),
            school_source VARCHAR(16),
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi),
            KEY idx_last_name (last_name),
            KEY idx_specialty (primary_specialty_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_npi_xwalk (
            npi BIGINT UNSIGNED NOT NULL,
            ind_pac_id VARCHAR(16),
            org_pac_id VARCHAR(16),
            nppes_last_update DATE,
            pdc_loaded_at DATETIME,
            nppes_loaded_at DATETIME,
            PRIMARY KEY (npi)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_network_npi (
            npi BIGINT UNSIGNED NOT NULL,
            customer_id VARCHAR(64) NOT NULL,
            PRIMARY KEY (customer_id, npi),
            KEY idx_npi (npi)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    ]


def create_schema(conn, mart_db: str = MART_DB) -> None:
    from provider_directory.db import ensure_mart_database

    ensure_mart_database(conn, mart_db)
    with conn.cursor() as cur:
        for stmt in ddl_statements(mart_db):
            cur.execute(stmt)
