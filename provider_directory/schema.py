"""DDL for CMS staging + mart tables. Writes only land in the mart DB."""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.settings import MART_CHARSET, MART_COLLATION, MART_DB, require_ident

TABLES = (
    "cms_pdc_clinician",
    "cms_pdc_facility_affil",
    "cms_nppes_type1",
    "pd_provider",
    "pd_npi_xwalk",
    "pd_network_npi",
    "pd_provider_practice",
    "pd_stg_window_claim",
    "pd_stg_visit",
    "pd_stg_panel_patient",
    "pd_stg_top_dx",
    "pd_stg_top_px",
    "pd_stg_visit_site",
    "pd_stg_npi_sl",
    "pd_stg_npi_wrvu",
    "pd_stg_site_wrvu",
    "pd_stg_npi_payor",
)

PHASE2_STAGING_TABLES = (
    "pd_stg_window_claim",
    "pd_stg_visit",
    "pd_stg_panel_patient",
    "pd_stg_top_dx",
    "pd_stg_top_px",
)

PHASE3_STAGING_TABLES = (
    "pd_stg_visit_site",
    "pd_stg_npi_sl",
)

PHASE4_STAGING_TABLES = (
    "pd_stg_npi_wrvu",
    "pd_stg_site_wrvu",
    "pd_stg_npi_payor",
)

STAGING_TABLES = PHASE2_STAGING_TABLES + PHASE3_STAGING_TABLES + PHASE4_STAGING_TABLES


def table_options() -> str:
    charset = require_ident(MART_CHARSET, "charset")
    collation = require_ident(MART_COLLATION, "collation")
    return f"ENGINE=InnoDB DEFAULT CHARSET={charset} COLLATE={collation}"


PD_PROVIDER_PHASE2_COLUMNS = (
    ("active_provider", "TINYINT NULL"),
    ("visits_total", "INT UNSIGNED NULL"),
    ("visits_top_diagnosis_1", "VARCHAR(10) NULL"),
    ("visits_top_diagnosis_1_name", "VARCHAR(80) NULL"),
    ("visits_top_diagnosis_2", "VARCHAR(10) NULL"),
    ("visits_top_diagnosis_2_name", "VARCHAR(80) NULL"),
    ("visits_top_diagnosis_3", "VARCHAR(10) NULL"),
    ("visits_top_diagnosis_3_name", "VARCHAR(80) NULL"),
    ("visits_top_procedure_1", "VARCHAR(10) NULL"),
    ("visits_top_procedure_1_name", "VARCHAR(80) NULL"),
    ("visits_top_procedure_2", "VARCHAR(10) NULL"),
    ("visits_top_procedure_2_name", "VARCHAR(80) NULL"),
    ("visits_top_procedure_3", "VARCHAR(10) NULL"),
    ("visits_top_procedure_3_name", "VARCHAR(80) NULL"),
    ("panel_size", "INT UNSIGNED NULL"),
    ("panel_average_age", "DECIMAL(5,1) NULL"),
    ("panel_percent_age_0_19", "DECIMAL(6,2) NULL"),
    ("panel_percent_age_20_44", "DECIMAL(6,2) NULL"),
    ("panel_percent_age_45_64", "DECIMAL(6,2) NULL"),
    ("panel_percent_age_65_84", "DECIMAL(6,2) NULL"),
    ("panel_percent_age_85_plus", "DECIMAL(6,2) NULL"),
    ("panel_percent_female", "DECIMAL(6,2) NULL"),
    ("panel_percent_male", "DECIMAL(6,2) NULL"),
)

PD_PROVIDER_PHASE3_COLUMNS = (
    ("provider_practices_total", "INT UNSIGNED NULL"),
)

PD_PROVIDER_PHASE4_COLUMNS = (
    ("wrvu_total", "DECIMAL(14,2) NULL"),
    ("wrvu_average", "DECIMAL(10,3) NULL"),
    ("wrvu_procedure_count", "INT UNSIGNED NULL"),
    ("visits_percent_third_party", "DECIMAL(6,2) NULL"),
    ("visits_percent_medicaid", "DECIMAL(6,2) NULL"),
    ("visits_percent_medicare_advantage", "DECIMAL(6,2) NULL"),
    ("visits_percent_medicare_traditional", "DECIMAL(6,2) NULL"),
    ("top_payer_name_1", "VARCHAR(140) NULL"),
    ("top_payer_percent_1", "DECIMAL(6,2) NULL"),
    ("top_payer_name_2", "VARCHAR(140) NULL"),
    ("top_payer_percent_2", "DECIMAL(6,2) NULL"),
    ("top_payer_name_3", "VARCHAR(140) NULL"),
    ("top_payer_percent_3", "DECIMAL(6,2) NULL"),
    ("primary_organization_id", "BIGINT NULL"),
    ("primary_organization_name", "VARCHAR(180) NULL"),
    ("primary_organization_npi", "BIGINT NULL"),
    ("primary_organization_parent_id", "INT NULL"),
    ("primary_organization_parent_name", "VARCHAR(80) NULL"),
)

PD_PRACTICE_PHASE4_COLUMNS = (
    ("wrvu_at_site", "DECIMAL(14,2) NULL"),
    ("wrvu_share_pct", "DECIMAL(6,2) NULL"),
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
        ) {table_options()}
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
        ) {table_options()}
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
        ) {table_options()}
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
            active_provider TINYINT NULL,
            visits_total INT UNSIGNED NULL,
            visits_top_diagnosis_1 VARCHAR(10) NULL,
            visits_top_diagnosis_1_name VARCHAR(80) NULL,
            visits_top_diagnosis_2 VARCHAR(10) NULL,
            visits_top_diagnosis_2_name VARCHAR(80) NULL,
            visits_top_diagnosis_3 VARCHAR(10) NULL,
            visits_top_diagnosis_3_name VARCHAR(80) NULL,
            visits_top_procedure_1 VARCHAR(10) NULL,
            visits_top_procedure_1_name VARCHAR(80) NULL,
            visits_top_procedure_2 VARCHAR(10) NULL,
            visits_top_procedure_2_name VARCHAR(80) NULL,
            visits_top_procedure_3 VARCHAR(10) NULL,
            visits_top_procedure_3_name VARCHAR(80) NULL,
            panel_size INT UNSIGNED NULL,
            panel_average_age DECIMAL(5,1) NULL,
            panel_percent_age_0_19 DECIMAL(6,2) NULL,
            panel_percent_age_20_44 DECIMAL(6,2) NULL,
            panel_percent_age_45_64 DECIMAL(6,2) NULL,
            panel_percent_age_65_84 DECIMAL(6,2) NULL,
            panel_percent_age_85_plus DECIMAL(6,2) NULL,
            panel_percent_female DECIMAL(6,2) NULL,
            panel_percent_male DECIMAL(6,2) NULL,
            provider_practices_total INT UNSIGNED NULL,
            wrvu_total DECIMAL(14,2) NULL,
            wrvu_average DECIMAL(10,3) NULL,
            wrvu_procedure_count INT UNSIGNED NULL,
            visits_percent_third_party DECIMAL(6,2) NULL,
            visits_percent_medicaid DECIMAL(6,2) NULL,
            visits_percent_medicare_advantage DECIMAL(6,2) NULL,
            visits_percent_medicare_traditional DECIMAL(6,2) NULL,
            top_payer_name_1 VARCHAR(140) NULL,
            top_payer_percent_1 DECIMAL(6,2) NULL,
            top_payer_name_2 VARCHAR(140) NULL,
            top_payer_percent_2 DECIMAL(6,2) NULL,
            top_payer_name_3 VARCHAR(140) NULL,
            top_payer_percent_3 DECIMAL(6,2) NULL,
            primary_organization_id BIGINT NULL,
            primary_organization_name VARCHAR(180) NULL,
            primary_organization_npi BIGINT NULL,
            primary_organization_parent_id INT NULL,
            primary_organization_parent_name VARCHAR(80) NULL,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi),
            KEY idx_last_name (last_name),
            KEY idx_specialty (primary_specialty_code),
            KEY idx_active (active_provider)
        ) {table_options()}
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
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_network_npi (
            npi BIGINT UNSIGNED NOT NULL,
            customer_id VARCHAR(64) NOT NULL,
            PRIMARY KEY (customer_id, npi),
            KEY idx_npi (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_window_claim (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            encounter_id BIGINT UNSIGNED,
            period_code INT,
            pat_id BIGINT UNSIGNED,
            age_code SMALLINT,
            gender_code VARCHAR(1),
            rendering_physician_code BIGINT UNSIGNED,
            referring_physician_code BIGINT UNSIGNED,
            encounter_rendering_physician_code BIGINT,
            encounter_diagnosis_code VARCHAR(10),
            encounter_work_procd_code VARCHAR(10),
            sl_code BIGINT UNSIGNED,
            PRIMARY KEY (id),
            KEY idx_enc (encounter_id),
            KEY idx_enc_rend (encounter_rendering_physician_code),
            KEY idx_rend (rendering_physician_code),
            KEY idx_refr (referring_physician_code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_visit (
            encounter_id BIGINT UNSIGNED NOT NULL,
            rendering_npi BIGINT UNSIGNED,
            dx VARCHAR(10),
            px VARCHAR(10),
            pat_id BIGINT UNSIGNED,
            period_code INT,
            PRIMARY KEY (encounter_id),
            KEY idx_rend (rendering_npi),
            KEY idx_dx (dx),
            KEY idx_px (px)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_panel_patient (
            npi BIGINT UNSIGNED NOT NULL,
            pat_id BIGINT UNSIGNED NOT NULL,
            age_code SMALLINT,
            gender_code VARCHAR(1),
            PRIMARY KEY (npi, pat_id)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_top_dx (
            npi BIGINT UNSIGNED NOT NULL,
            code VARCHAR(10) NOT NULL,
            name VARCHAR(80),
            visit_count INT UNSIGNED NOT NULL,
            rk TINYINT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, rk),
            KEY idx_npi (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_top_px (
            npi BIGINT UNSIGNED NOT NULL,
            code VARCHAR(10) NOT NULL,
            name VARCHAR(80),
            visit_count INT UNSIGNED NOT NULL,
            rk TINYINT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, rk),
            KEY idx_npi (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_provider_practice (
            npi BIGINT UNSIGNED NOT NULL,
            site_rank TINYINT UNSIGNED NOT NULL,
            sl_code BIGINT UNSIGNED,
            cluster_key VARCHAR(180),
            name VARCHAR(254),
            street VARCHAR(80),
            city VARCHAR(80),
            county VARCHAR(80),
            state CHAR(2),
            zip VARCHAR(5),
            latitude DECIMAL(10,6),
            longitude DECIMAL(10,6),
            phone VARCHAR(20),
            work_type VARCHAR(80),
            visits_at_site INT UNSIGNED NOT NULL DEFAULT 0,
            visit_share_pct DECIMAL(6,2),
            wrvu_at_site DECIMAL(14,2) NULL,
            wrvu_share_pct DECIMAL(6,2) NULL,
            npi_type VARCHAR(8),
            location_source VARCHAR(16),
            location_flag VARCHAR(20),
            phone_source VARCHAR(16),
            needs_geocode TINYINT NOT NULL DEFAULT 0,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, site_rank),
            KEY idx_sl (sl_code),
            KEY idx_state_zip (state, zip)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_visit_site (
            encounter_id BIGINT UNSIGNED NOT NULL,
            rendering_npi BIGINT UNSIGNED NOT NULL,
            sl_code BIGINT UNSIGNED NOT NULL,
            PRIMARY KEY (encounter_id),
            KEY idx_rend_sl (rendering_npi, sl_code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_sl (
            npi BIGINT UNSIGNED NOT NULL,
            sl_code BIGINT UNSIGNED NOT NULL,
            visits INT UNSIGNED NOT NULL,
            cluster_key VARCHAR(180) NOT NULL,
            name VARCHAR(254),
            street VARCHAR(80),
            city VARCHAR(80),
            county VARCHAR(80),
            state CHAR(2),
            zip VARCHAR(5),
            latitude DECIMAL(10,6),
            longitude DECIMAL(10,6),
            work_type VARCHAR(80),
            npi_type VARCHAR(8),
            needs_geocode TINYINT NOT NULL DEFAULT 0,
            PRIMARY KEY (npi, sl_code),
            KEY idx_cluster (npi, cluster_key)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_wrvu (
            npi BIGINT UNSIGNED NOT NULL,
            total_wrvu DECIMAL(14,4) NOT NULL,
            procedure_count INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_site_wrvu (
            npi BIGINT UNSIGNED NOT NULL,
            sl_code BIGINT UNSIGNED NOT NULL,
            total_wrvu DECIMAL(14,4) NOT NULL,
            procedure_count INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, sl_code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_payor (
            npi BIGINT UNSIGNED NOT NULL,
            is_payor_code SMALLINT NOT NULL,
            payor_parent_name VARCHAR(140) NOT NULL,
            claim_count INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, is_payor_code, payor_parent_name),
            KEY idx_npi (npi)
        ) {table_options()}
        """,
    ]


def migrate_phase2_columns(conn, mart_db: str = MART_DB) -> None:
    """Add Phase 2 columns to a pd_provider that was created in Phase 1."""
    table = f"{quote_ident(mart_db)}.pd_provider"
    with conn.cursor() as cur:
        for name, definition in PD_PROVIDER_PHASE2_COLUMNS:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
            )


def migrate_phase3_columns(conn, mart_db: str = MART_DB) -> None:
    """Add Phase 3 columns to a pd_provider that was created before locations."""
    table = f"{quote_ident(mart_db)}.pd_provider"
    with conn.cursor() as cur:
        for name, definition in PD_PROVIDER_PHASE3_COLUMNS:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
            )


def drop_staging_tables(conn, mart_db: str = MART_DB, tables: tuple[str, ...] | None = None) -> None:
    db = quote_ident(mart_db)
    names = tables if tables is not None else STAGING_TABLES
    with conn.cursor() as cur:
        for table in names:
            cur.execute(f"DROP TABLE IF EXISTS {db}.{quote_ident(table)}")


def migrate_phase4_columns(conn, mart_db: str = MART_DB) -> None:
    """Add Phase 4 columns to pd_provider / pd_provider_practice."""
    provider = f"{quote_ident(mart_db)}.pd_provider"
    practice = f"{quote_ident(mart_db)}.pd_provider_practice"
    with conn.cursor() as cur:
        for name, definition in PD_PROVIDER_PHASE4_COLUMNS:
            cur.execute(
                f"ALTER TABLE {provider} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
            )
        cur.execute(
            """
            SELECT 1 AS ok
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'pd_provider_practice'
            """,
            (mart_db,),
        )
        if cur.fetchone():
            for name, definition in PD_PRACTICE_PHASE4_COLUMNS:
                cur.execute(
                    f"ALTER TABLE {practice} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
                )


def drop_phase3_staging(conn, mart_db: str = MART_DB) -> None:
    drop_staging_tables(conn, mart_db, PHASE3_STAGING_TABLES)


def drop_phase4_staging(conn, mart_db: str = MART_DB) -> None:
    drop_staging_tables(conn, mart_db, PHASE4_STAGING_TABLES)


def convert_persistent_collation(conn, mart_db: str = MART_DB) -> None:
    """Bring existing Phase 1 tables onto utf8mb4_unicode_520_ci. Skip staging (dropped/recreated)."""
    charset = require_ident(MART_CHARSET, "charset")
    collation = require_ident(MART_COLLATION, "collation")
    db = quote_ident(mart_db)
    persistent = [name for name in TABLES if name not in STAGING_TABLES]
    with conn.cursor() as cur:
        for table in persistent:
            cur.execute(
                """
                SELECT TABLE_COLLATION AS coll
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """,
                (mart_db, table),
            )
            row = cur.fetchone()
            if not row or row["coll"] == collation:
                continue
            cur.execute(
                f"ALTER TABLE {db}.{quote_ident(table)} "
                f"CONVERT TO CHARACTER SET {charset} COLLATE {collation}"
            )


def create_schema(conn, mart_db: str = MART_DB) -> None:
    from provider_directory.db import ensure_mart_database

    ensure_mart_database(conn, mart_db)
    with conn.cursor() as cur:
        for stmt in ddl_statements(mart_db):
            cur.execute(stmt)
    migrate_phase2_columns(conn, mart_db)
    migrate_phase3_columns(conn, mart_db)
    migrate_phase4_columns(conn, mart_db)
    convert_persistent_collation(conn, mart_db)
