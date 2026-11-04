"""DDL for CMS staging + mart tables. Writes only land in the mart DB."""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.settings import MART_CHARSET, MART_COLLATION, MART_DB, require_ident, TOP_CODE_KINDS, TOP_N

TABLES = (
    "cms_pdc_clinician",
    "cms_pdc_facility_affil",
    "cms_nppes_type1",
    "cms_pdc_mips",
    "cms_pdc_utilization",
    "cms_open_payments",
    "pd_provider",
    "pd_npi_xwalk",
    "pd_network_npi",
    "pd_refresh_state",
    "pd_provider_practice",
    "pd_provider_group_practice",
    "pd_provider_hospital_affiliation",
    "pd_provider_referral",
    "pd_provider_utilization",
    "pd_group_practice",
    "pd_src_physician",
    "pd_src_group_practice",
    "pd_stg_window_claim",
    "pd_stg_visit",
    "pd_stg_panel_patient",
    "pd_stg_top_dx",
    "pd_stg_top_px",
    "pd_stg_top_primary_px",
    "pd_stg_visit_site",
    "pd_stg_npi_sl",
    "pd_stg_npi_px_line",
    "pd_stg_npi_wrvu",
    "pd_stg_site_wrvu",
    "pd_stg_npi_payor",
    "pd_stg_visit_date",
    "pd_stg_npi_px_line_prior",
    "pd_stg_npi_dow",
    "pd_stg_site_dow",
    "pd_stg_referral_edge",
    "pd_stg_npi_wrvu_prior",
    "pd_stg_specialty_wrvu",
    "pd_stg_npi_percentile",
    "pd_stg_npi_avg_percentile",
    "pd_stg_npi_visits_percentile",
)

PHASE2_STAGING_TABLES = (
    "pd_stg_window_claim",
    "pd_stg_visit",
    "pd_stg_panel_patient",
    "pd_stg_top_dx",
    "pd_stg_top_px",
    "pd_stg_top_primary_px",
)

PHASE2_DERIVED_TABLES = (
    "pd_stg_visit",
    "pd_stg_panel_patient",
    "pd_stg_top_dx",
    "pd_stg_top_px",
    "pd_stg_top_primary_px",
)

TOP_CODE_TABLES = tuple(table for _prefix, table in TOP_CODE_KINDS)

PHASE3_STAGING_TABLES = (
    "pd_stg_visit_site",
    "pd_stg_npi_sl",
)

PHASE4_STAGING_TABLES = (
    "pd_stg_npi_px_line",
    "pd_stg_npi_wrvu",
    "pd_stg_site_wrvu",
    "pd_stg_npi_payor",
)

PHASE5_STAGING_TABLES = (
    "pd_stg_npi_dow",
    "pd_stg_site_dow",
    "pd_stg_npi_wrvu_prior",
    "pd_stg_specialty_wrvu",
    "pd_stg_npi_percentile",
    "pd_stg_npi_avg_percentile",
    "pd_stg_npi_visits_percentile",
)

# Kept across phase5 runs (reuse_cached); rebuilt only with --no-reuse / --rebuild-edges.
PHASE5_CACHED_STAGING_TABLES = (
    "pd_stg_visit_date",
    "pd_stg_npi_px_line_prior",
    "pd_stg_referral_edge",
)

STAGING_TABLES = (
    PHASE2_STAGING_TABLES
    + PHASE3_STAGING_TABLES
    + PHASE4_STAGING_TABLES
    + PHASE5_STAGING_TABLES
    + PHASE5_CACHED_STAGING_TABLES
)


MART_ENGINE = "MyISAM"


def table_options() -> str:
    charset = require_ident(MART_CHARSET, "charset")
    collation = require_ident(MART_COLLATION, "collation")
    return f"ENGINE={MART_ENGINE} DEFAULT CHARSET={charset} COLLATE={collation}"


def _top_code_columns() -> tuple[tuple[str, str], ...]:
    cols: list[tuple[str, str]] = []
    for prefix, _table in TOP_CODE_KINDS:
        for i in range(1, TOP_N + 1):
            cols.append((f"{prefix}_{i}", "VARCHAR(10) NULL"))
            cols.append((f"{prefix}_{i}_name", "VARCHAR(80) NULL"))
    return tuple(cols)


TOP_CODE_COLUMNS = _top_code_columns()

# From the reference server (sync --ref). Always on the profile, blank when the
# master file has nothing for the NPI.
PD_PROVIDER_REFERENCE_COLUMNS = (
    ("affiliation_code", "BIGINT UNSIGNED NULL"),
    ("affiliation_name", "VARCHAR(180) NULL"),
    ("affiliation_source", "VARCHAR(16) NULL"),
    ("reference_group_id", "BIGINT UNSIGNED NULL"),
    ("reference_group_name", "VARCHAR(180) NULL"),
    # The warehouse billing organization (physician_primary_affiliation), kept as
    # "bills under" now that primary_organization_* is the master-file group practice.
    ("billing_organization_id", "BIGINT NULL"),
    ("billing_organization_name", "VARCHAR(180) NULL"),
)

PD_PROVIDER_PHASE2_COLUMNS = (
    ("active_provider", "TINYINT NULL"),
    ("visits_total", "INT UNSIGNED NULL"),
    *TOP_CODE_COLUMNS,
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
    ("visits_percent_hmo", "DECIMAL(6,2) NULL"),
    ("visits_percent_medicare_other", "DECIMAL(6,2) NULL"),
    ("top_payer_name_1", "VARCHAR(140) NULL"),
    ("top_payer_percent_1", "DECIMAL(6,2) NULL"),
    ("top_payer_name_2", "VARCHAR(140) NULL"),
    ("top_payer_percent_2", "DECIMAL(6,2) NULL"),
    ("top_payer_name_3", "VARCHAR(140) NULL"),
    ("top_payer_percent_3", "DECIMAL(6,2) NULL"),
    ("top_payer_name_4", "VARCHAR(140) NULL"),
    ("top_payer_percent_4", "DECIMAL(6,2) NULL"),
    ("top_payer_name_5", "VARCHAR(140) NULL"),
    ("top_payer_percent_5", "DECIMAL(6,2) NULL"),
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

PD_PROVIDER_PHASE5_COLUMNS = (
    ("visits_percent_monday", "DECIMAL(6,2) NULL"),
    ("visits_percent_tuesday", "DECIMAL(6,2) NULL"),
    ("visits_percent_wednesday", "DECIMAL(6,2) NULL"),
    ("visits_percent_thursday", "DECIMAL(6,2) NULL"),
    ("visits_percent_friday", "DECIMAL(6,2) NULL"),
    ("visits_percent_saturday", "DECIMAL(6,2) NULL"),
    ("visits_percent_sunday", "DECIMAL(6,2) NULL"),
    ("wrvu_prior_year_total", "DECIMAL(14,2) NULL"),
    ("wrvu_prior_year_average", "DECIMAL(10,3) NULL"),
    ("wrvu_prior_year_procedure_count", "INT UNSIGNED NULL"),
    ("wrvu_yoy_change_pct", "DECIMAL(12,2) NULL"),
    ("wrvu_state_specialty_average", "DECIMAL(14,2) NULL"),
    ("wrvu_state_specialty_average_per_procedure", "DECIMAL(10,3) NULL"),
    ("wrvu_state_specialty_median_per_procedure", "DECIMAL(10,3) NULL"),
    ("wrvu_state_specialty_p25_per_procedure", "DECIMAL(10,3) NULL"),
    ("wrvu_state_specialty_p75_per_procedure", "DECIMAL(10,3) NULL"),
    ("wrvu_average_specialty_percentile", "DECIMAL(5,1) NULL"),
    ("wrvu_state_specialty_median", "DECIMAL(14,2) NULL"),
    ("wrvu_state_specialty_p25", "DECIMAL(14,2) NULL"),
    ("wrvu_state_specialty_p75", "DECIMAL(14,2) NULL"),
    ("wrvu_state_specialty_npi_count", "INT UNSIGNED NULL"),
    ("wrvu_specialty_percentile", "DECIMAL(5,1) NULL"),
    ("visits_specialty_percentile", "DECIMAL(5,1) NULL"),
    ("activity_specialty_percentile", "DECIMAL(5,1) NULL"),
)

PD_PRACTICE_PHASE5_COLUMNS = (
    ("visits_percent_monday", "DECIMAL(6,2) NULL"),
    ("visits_percent_tuesday", "DECIMAL(6,2) NULL"),
    ("visits_percent_wednesday", "DECIMAL(6,2) NULL"),
    ("visits_percent_thursday", "DECIMAL(6,2) NULL"),
    ("visits_percent_friday", "DECIMAL(6,2) NULL"),
    ("visits_percent_saturday", "DECIMAL(6,2) NULL"),
    ("visits_percent_sunday", "DECIMAL(6,2) NULL"),
)

CMS_PDC_CLINICIAN_EXTRAS_COLUMNS = (
    ("sec_spec_1", "VARCHAR(120) NULL"),
    ("sec_spec_2", "VARCHAR(120) NULL"),
    ("sec_spec_3", "VARCHAR(120) NULL"),
    ("sec_spec_4", "VARCHAR(120) NULL"),
)

PD_PROVIDER_EXTRAS_COLUMNS = (
    ("group_size", "INT NULL"),
    ("telehealth_offered", "TINYINT NULL"),
    ("secondary_specialty_1", "VARCHAR(120) NULL"),
    ("secondary_specialty_2", "VARCHAR(120) NULL"),
    ("secondary_specialty_3", "VARCHAR(120) NULL"),
    ("secondary_specialty_4", "VARCHAR(120) NULL"),
    ("visits_new_patient", "INT UNSIGNED NULL"),
    ("visits_established", "INT UNSIGNED NULL"),
    ("visits_percent_new_patient", "DECIMAL(6,2) NULL"),
    ("visits_percent_office", "DECIMAL(6,2) NULL"),
    ("visits_percent_hopd", "DECIMAL(6,2) NULL"),
    ("visits_percent_asc", "DECIMAL(6,2) NULL"),
    ("visits_percent_ed", "DECIMAL(6,2) NULL"),
    ("visits_percent_telehealth", "DECIMAL(6,2) NULL"),
    ("visits_percent_inpatient", "DECIMAL(6,2) NULL"),
    ("visits_percent_lab", "DECIMAL(6,2) NULL"),
    ("visits_percent_other_pos", "DECIMAL(6,2) NULL"),
    ("mips_final_score", "DECIMAL(6,2) NULL"),
    ("mips_quality_score", "DECIMAL(6,2) NULL"),
    ("open_payments_year", "SMALLINT NULL"),
    ("open_payments_general_total", "DECIMAL(14,2) NULL"),
    ("open_payments_research_total", "DECIMAL(14,2) NULL"),
    ("open_payments_ownership_total", "DECIMAL(14,2) NULL"),
    ("open_payments_count", "INT UNSIGNED NULL"),
)


def top_code_ddl() -> str:
    return ",\n            ".join(f"{name} {definition}" for name, definition in TOP_CODE_COLUMNS) + ","


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
            sec_spec_1 VARCHAR(120),
            sec_spec_2 VARCHAR(120),
            sec_spec_3 VARCHAR(120),
            sec_spec_4 VARCHAR(120),
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
            {top_code_ddl()}
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
            visits_percent_hmo DECIMAL(6,2) NULL,
            visits_percent_medicare_other DECIMAL(6,2) NULL,
            top_payer_name_1 VARCHAR(140) NULL,
            top_payer_percent_1 DECIMAL(6,2) NULL,
            top_payer_name_2 VARCHAR(140) NULL,
            top_payer_percent_2 DECIMAL(6,2) NULL,
            top_payer_name_3 VARCHAR(140) NULL,
            top_payer_percent_3 DECIMAL(6,2) NULL,
            top_payer_name_4 VARCHAR(140) NULL,
            top_payer_percent_4 DECIMAL(6,2) NULL,
            top_payer_name_5 VARCHAR(140) NULL,
            top_payer_percent_5 DECIMAL(6,2) NULL,
            primary_organization_id BIGINT NULL,
            primary_organization_name VARCHAR(180) NULL,
            primary_organization_npi BIGINT NULL,
            primary_organization_parent_id INT NULL,
            primary_organization_parent_name VARCHAR(80) NULL,
            visits_percent_monday DECIMAL(6,2) NULL,
            visits_percent_tuesday DECIMAL(6,2) NULL,
            visits_percent_wednesday DECIMAL(6,2) NULL,
            visits_percent_thursday DECIMAL(6,2) NULL,
            visits_percent_friday DECIMAL(6,2) NULL,
            visits_percent_saturday DECIMAL(6,2) NULL,
            visits_percent_sunday DECIMAL(6,2) NULL,
            wrvu_prior_year_total DECIMAL(14,2) NULL,
            wrvu_prior_year_average DECIMAL(10,3) NULL,
            wrvu_prior_year_procedure_count INT UNSIGNED NULL,
            wrvu_yoy_change_pct DECIMAL(12,2) NULL,
            wrvu_state_specialty_average DECIMAL(14,2) NULL,
            wrvu_state_specialty_median DECIMAL(14,2) NULL,
            wrvu_state_specialty_p25 DECIMAL(14,2) NULL,
            wrvu_state_specialty_p75 DECIMAL(14,2) NULL,
            wrvu_state_specialty_npi_count INT UNSIGNED NULL,
            wrvu_specialty_percentile DECIMAL(5,1) NULL,
            visits_specialty_percentile DECIMAL(5,1) NULL,
            activity_specialty_percentile DECIMAL(5,1) NULL,
            group_size INT NULL,
            telehealth_offered TINYINT NULL,
            secondary_specialty_1 VARCHAR(120) NULL,
            secondary_specialty_2 VARCHAR(120) NULL,
            secondary_specialty_3 VARCHAR(120) NULL,
            secondary_specialty_4 VARCHAR(120) NULL,
            visits_new_patient INT UNSIGNED NULL,
            visits_established INT UNSIGNED NULL,
            visits_percent_new_patient DECIMAL(6,2) NULL,
            visits_percent_office DECIMAL(6,2) NULL,
            visits_percent_hopd DECIMAL(6,2) NULL,
            visits_percent_asc DECIMAL(6,2) NULL,
            visits_percent_ed DECIMAL(6,2) NULL,
            visits_percent_telehealth DECIMAL(6,2) NULL,
            visits_percent_inpatient DECIMAL(6,2) NULL,
            visits_percent_lab DECIMAL(6,2) NULL,
            visits_percent_other_pos DECIMAL(6,2) NULL,
            mips_final_score DECIMAL(6,2) NULL,
            mips_quality_score DECIMAL(6,2) NULL,
            open_payments_year SMALLINT NULL,
            open_payments_general_total DECIMAL(14,2) NULL,
            open_payments_research_total DECIMAL(14,2) NULL,
            open_payments_ownership_total DECIMAL(14,2) NULL,
            open_payments_count INT UNSIGNED NULL,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi),
            KEY idx_last_name (last_name),
            KEY idx_specialty (primary_specialty_code),
            KEY idx_active (active_provider),
            KEY idx_active_visits (active_provider, visits_total),
            KEY idx_spec_visits (primary_specialty_code, visits_total),
            KEY idx_org (primary_organization_id, visits_total)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.cms_pdc_mips (
            npi BIGINT UNSIGNED NOT NULL,
            org_pac_id VARCHAR(16) NOT NULL DEFAULT '',
            final_score DECIMAL(6,2),
            quality_score DECIMAL(6,2),
            loaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, org_pac_id),
            KEY idx_npi (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.cms_pdc_utilization (
            npi BIGINT UNSIGNED NOT NULL,
            procedure_category VARCHAR(180) NOT NULL,
            count_label VARCHAR(32),
            percentile SMALLINT,
            profile_display CHAR(1),
            loaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, procedure_category),
            KEY idx_npi (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.cms_open_payments (
            npi BIGINT UNSIGNED NOT NULL,
            program_year SMALLINT NOT NULL,
            payment_kind VARCHAR(16) NOT NULL,
            total DECIMAL(14,2) NOT NULL DEFAULT 0,
            payment_count INT UNSIGNED NOT NULL DEFAULT 0,
            loaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, program_year, payment_kind),
            KEY idx_npi (npi)
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
        CREATE TABLE IF NOT EXISTS {db}.pd_refresh_state (
            id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
            window_start INT NOT NULL,
            window_end INT NOT NULL,
            prior_window_start INT NOT NULL,
            prior_window_end INT NOT NULL,
            warehouse_max_period INT NULL,
            warehouse_source VARCHAR(80) NULL,
            slide_available TINYINT NOT NULL DEFAULT 0,
            last_action VARCHAR(32) NULL,
            last_indexes_at DATETIME NULL,
            last_slide_at DATETIME NULL,
            notes VARCHAR(255) NULL,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_src_physician (
            npi BIGINT UNSIGNED NOT NULL,
            npi_type VARCHAR(25) NULL,
            first_name VARCHAR(30) NULL,
            middle_name VARCHAR(15) NULL,
            last_name VARCHAR(254) NULL,
            suffix VARCHAR(4) NULL,
            specialty_primary VARCHAR(4) NULL,
            specialty_grp_code INT NULL,
            specialty_grp_abbr VARCHAR(4) NULL,
            specialty_grp_name VARCHAR(200) NULL,
            npi_spec_grp_code VARCHAR(25) NULL,
            npi_spec_grp_name VARCHAR(80) NULL,
            taxonomy_cat VARCHAR(20) NULL,
            taxonomy_cat_name VARCHAR(80) NULL,
            degree_code SMALLINT NULL,
            degree_abbr VARCHAR(10) NULL,
            degree_name VARCHAR(30) NULL,
            zip_code VARCHAR(5) NULL,
            street VARCHAR(80) NULL,
            city VARCHAR(80) NULL,
            county_code VARCHAR(6) NULL,
            county_name VARCHAR(80) NULL,
            state_abbr VARCHAR(2) NULL,
            physician_name VARCHAR(253) NULL,
            dba_name VARCHAR(253) NULL,
            latitude DECIMAL(10,6) NULL,
            longitude DECIMAL(10,6) NULL,
            phone_number VARCHAR(30) NULL,
            group_prac_code BIGINT UNSIGNED NULL,
            group_prac_name VARCHAR(180) NULL,
            inpatient INT NULL,
            outpatient INT NULL,
            ed INT NULL,
            claim INT UNSIGNED NULL,
            affiliation_code BIGINT UNSIGNED NULL,
            affiliation_name VARCHAR(255) NULL,
            affiliation_dba_name VARCHAR(255) NULL,
            rec_create_date DATETIME NULL,
            rec_modify_date DATETIME NULL,
            copied_at DATETIME NULL,
            PRIMARY KEY (npi),
            KEY idx_state_type (state_abbr, npi_type),
            KEY idx_group (group_prac_code),
            KEY idx_affiliation (affiliation_code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_src_group_practice (
            rec_id BIGINT UNSIGNED NOT NULL,
            group_pac_id VARCHAR(10) NULL,
            group_enrollment_id VARCHAR(15) NULL,
            group_name VARCHAR(70) NULL,
            group_state VARCHAR(2) NULL,
            group_member_count BIGINT UNSIGNED NULL,
            record_type VARCHAR(19) NULL,
            individual_enrollment_id VARCHAR(15) NULL,
            npi BIGINT UNSIGNED NULL,
            first_name VARCHAR(25) NULL,
            last_name VARCHAR(35) NULL,
            individual_state VARCHAR(2) NULL,
            specialty_description VARCHAR(150) NULL,
            employer_associations INT UNSIGNED NULL,
            copied_at DATETIME NULL,
            PRIMARY KEY (rec_id),
            KEY idx_npi (npi),
            KEY idx_group (group_pac_id)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_window_claim (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            encounter_id DECIMAL(25,0),
            period_code INT,
            pat_id DECIMAL(25,0),
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
            KEY idx_refr (referring_physician_code),
            KEY idx_period (period_code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_visit (
            encounter_id DECIMAL(25,0) NOT NULL,
            rendering_npi BIGINT UNSIGNED,
            dx VARCHAR(10),
            px VARCHAR(10),
            pat_id DECIMAL(25,0),
            period_code INT,
            PRIMARY KEY (encounter_id),
            KEY idx_rend (rendering_npi),
            KEY idx_dx (dx),
            KEY idx_px (px),
            KEY idx_period (period_code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_panel_patient (
            npi BIGINT UNSIGNED NOT NULL,
            pat_id DECIMAL(25,0) NOT NULL,
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
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_top_primary_px (
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
            visits_percent_monday DECIMAL(6,2) NULL,
            visits_percent_tuesday DECIMAL(6,2) NULL,
            visits_percent_wednesday DECIMAL(6,2) NULL,
            visits_percent_thursday DECIMAL(6,2) NULL,
            visits_percent_friday DECIMAL(6,2) NULL,
            visits_percent_saturday DECIMAL(6,2) NULL,
            visits_percent_sunday DECIMAL(6,2) NULL,
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
        CREATE TABLE IF NOT EXISTS {db}.pd_provider_group_practice (
            npi BIGINT UNSIGNED NOT NULL,
            org_rank TINYINT UNSIGNED NOT NULL,
            organization_id BIGINT NOT NULL,
            organization_npi BIGINT NULL,
            organization_name VARCHAR(180),
            billing_type CHAR(1) NULL,
            is_primary TINYINT NOT NULL DEFAULT 0,
            cases INT UNSIGNED NULL,
            max_period_code INT UNSIGNED NULL,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, org_rank),
            KEY idx_org (organization_id),
            KEY idx_npi_org (npi, organization_id)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_provider_hospital_affiliation (
            npi BIGINT UNSIGNED NOT NULL,
            affiliation_rank TINYINT UNSIGNED NOT NULL,
            hospital_system_name VARCHAR(180) NOT NULL,
            facility_name VARCHAR(180) NULL,
            sl_code BIGINT UNSIGNED NULL,
            visits_at_system INT UNSIGNED NOT NULL DEFAULT 0,
            visit_share_pct DECIMAL(6,2) NULL,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, affiliation_rank),
            KEY idx_system (hospital_system_name)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_visit_site (
            encounter_id DECIMAL(25,0) NOT NULL,
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
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_px_line (
            npi BIGINT UNSIGNED NOT NULL,
            sl_code BIGINT UNSIGNED NOT NULL,
            code VARCHAR(10) NOT NULL,
            line_count INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, sl_code, code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_px_line_prior (
            npi BIGINT UNSIGNED NOT NULL,
            code VARCHAR(10) NOT NULL,
            line_count INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, code)
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
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_provider_referral (
            npi BIGINT UNSIGNED NOT NULL,
            direction VARCHAR(3) NOT NULL,
            peer_rank TINYINT UNSIGNED NOT NULL,
            peer_npi BIGINT UNSIGNED NOT NULL,
            peer_name VARCHAR(180),
            peer_specialty VARCHAR(120),
            patient_count INT UNSIGNED NOT NULL DEFAULT 0,
            claim_count INT UNSIGNED NULL,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, direction, peer_rank),
            KEY idx_peer (peer_npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_provider_utilization (
            npi BIGINT UNSIGNED NOT NULL,
            rk TINYINT UNSIGNED NOT NULL,
            procedure_category VARCHAR(180) NOT NULL,
            count_label VARCHAR(32),
            percentile SMALLINT,
            profile_display CHAR(1),
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (npi, rk),
            KEY idx_npi (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_group_practice (
            member_scope VARCHAR(8) NOT NULL,
            organization_id BIGINT NOT NULL,
            organization_name VARCHAR(180) NULL,
            organization_npi BIGINT NULL,
            parent_id INT NULL,
            parent_name VARCHAR(80) NULL,
            provider_count INT UNSIGNED NOT NULL DEFAULT 0,
            active_provider_count INT UNSIGNED NOT NULL DEFAULT 0,
            in_system_provider_count INT UNSIGNED NOT NULL DEFAULT 0,
            visits_total BIGINT UNSIGNED NOT NULL DEFAULT 0,
            panel_size BIGINT UNSIGNED NOT NULL DEFAULT 0,
            wrvu_total DECIMAL(16,2) NULL,
            visits_per_provider DECIMAL(12,1) NULL,
            visits_percentile DECIMAL(5,1) NULL,
            activity_percentile DECIMAL(5,1) NULL,
            state CHAR(2) NULL,
            refreshed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (member_scope, organization_id),
            KEY idx_scope_state_visits (member_scope, state, visits_total),
            KEY idx_scope_visits (member_scope, visits_total),
            KEY idx_scope_name (member_scope, organization_name),
            KEY idx_scope_parent (member_scope, parent_name),
            KEY idx_scope_providers (member_scope, provider_count)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_visit_date (
            encounter_id DECIMAL(25,0) NOT NULL,
            service_end_date DATE NOT NULL,
            PRIMARY KEY (encounter_id)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_dow (
            npi BIGINT UNSIGNED NOT NULL,
            dow TINYINT UNSIGNED NOT NULL,
            visits INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, dow)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_site_dow (
            npi BIGINT UNSIGNED NOT NULL,
            sl_code BIGINT UNSIGNED NOT NULL,
            dow TINYINT UNSIGNED NOT NULL,
            visits INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, sl_code, dow)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_referral_edge (
            npi BIGINT UNSIGNED NOT NULL,
            direction VARCHAR(3) NOT NULL,
            peer_npi BIGINT UNSIGNED NOT NULL,
            patient_count INT UNSIGNED NOT NULL,
            claim_count INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi, direction, peer_npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_wrvu_prior (
            npi BIGINT UNSIGNED NOT NULL,
            total_wrvu DECIMAL(14,4) NOT NULL,
            procedure_count INT UNSIGNED NOT NULL,
            PRIMARY KEY (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_specialty_wrvu (
            specialty_code VARCHAR(25) NOT NULL,
            npi_count INT UNSIGNED NOT NULL,
            avg_wrvu DECIMAL(14,2) NOT NULL,
            avg_wrvu_per_procedure DECIMAL(10,3) NULL,
            p25_wrvu DECIMAL(14,2) NULL,
            median_wrvu DECIMAL(14,2) NULL,
            p75_wrvu DECIMAL(14,2) NULL,
            p25_wrvu_per_procedure DECIMAL(10,3) NULL,
            median_wrvu_per_procedure DECIMAL(10,3) NULL,
            p75_wrvu_per_procedure DECIMAL(10,3) NULL,
            PRIMARY KEY (specialty_code)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_percentile (
            npi BIGINT UNSIGNED NOT NULL,
            pct DECIMAL(5,1) NOT NULL,
            PRIMARY KEY (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_avg_percentile (
            npi BIGINT UNSIGNED NOT NULL,
            pct DECIMAL(5,1) NOT NULL,
            PRIMARY KEY (npi)
        ) {table_options()}
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {db}.pd_stg_npi_visits_percentile (
            npi BIGINT UNSIGNED NOT NULL,
            pct DECIMAL(5,1) NOT NULL,
            PRIMARY KEY (npi)
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


def migrate_phase5_columns(conn, mart_db: str = MART_DB) -> None:
    """Add Phase 5 columns to pd_provider / pd_provider_practice."""
    provider = f"{quote_ident(mart_db)}.pd_provider"
    practice = f"{quote_ident(mart_db)}.pd_provider_practice"
    with conn.cursor() as cur:
        for name, definition in PD_PROVIDER_PHASE5_COLUMNS:
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
            for name, definition in PD_PRACTICE_PHASE5_COLUMNS:
                cur.execute(
                    f"ALTER TABLE {practice} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
                )
        cur.execute(
            f"ALTER TABLE {provider} MODIFY COLUMN wrvu_yoy_change_pct DECIMAL(12,2) NULL"
        )


GROUP_SUMMARY_EXTRA_COLUMNS = (("state", "CHAR(2) NULL"),)


def migrate_group_summary_columns(conn, mart_db: str = MART_DB) -> None:
    table = f"{quote_ident(mart_db)}.pd_group_practice"
    with conn.cursor() as cur:
        for name, definition in GROUP_SUMMARY_EXTRA_COLUMNS:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_scope_state_visits ON {table} (member_scope, state, visits_total)")


def migrate_reference_columns(conn, mart_db: str = MART_DB) -> None:
    """Add the reference-server columns (affiliation, reference group) to pd_provider."""
    provider = f"{quote_ident(mart_db)}.pd_provider"
    with conn.cursor() as cur:
        for name, definition in PD_PROVIDER_REFERENCE_COLUMNS:
            cur.execute(
                f"ALTER TABLE {provider} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
            )


def migrate_extras_columns(conn, mart_db: str = MART_DB) -> None:
    """Add cheap extras columns to existing pd_provider / cms_pdc_clinician."""
    provider = f"{quote_ident(mart_db)}.pd_provider"
    clinician = f"{quote_ident(mart_db)}.cms_pdc_clinician"
    with conn.cursor() as cur:
        for name, definition in CMS_PDC_CLINICIAN_EXTRAS_COLUMNS:
            cur.execute(
                f"ALTER TABLE {clinician} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
            )
        for name, definition in PD_PROVIDER_EXTRAS_COLUMNS:
            cur.execute(
                f"ALTER TABLE {provider} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {definition}"
            )


def drop_phase2_derived(conn, mart_db: str = MART_DB) -> None:
    drop_staging_tables(conn, mart_db, PHASE2_DERIVED_TABLES)


def drop_phase3_staging(conn, mart_db: str = MART_DB) -> None:
    drop_staging_tables(conn, mart_db, PHASE3_STAGING_TABLES)


def drop_phase4_staging(conn, mart_db: str = MART_DB) -> None:
    drop_staging_tables(conn, mart_db, PHASE4_STAGING_TABLES)


def drop_phase5_staging(conn, mart_db: str = MART_DB) -> None:
    drop_staging_tables(conn, mart_db, PHASE5_STAGING_TABLES)


def drop_phase5_cached(conn, mart_db: str = MART_DB) -> None:
    drop_staging_tables(conn, mart_db, PHASE5_CACHED_STAGING_TABLES)


MART_INDEXES = (
    ("pd_provider", "idx_active_visits", "active_provider, visits_total"),
    ("pd_provider", "idx_spec_visits", "primary_specialty_code, visits_total"),
    ("pd_provider", "idx_org", "primary_organization_id, visits_total"),
    ("pd_stg_window_claim", "idx_period", "period_code"),
    ("pd_stg_visit", "idx_period", "period_code"),
)

STAGING_INDEX_TABLES = frozenset({"pd_stg_window_claim", "pd_stg_visit"})


def index_exists(conn, mart_db: str, table: str, index_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 AS ok
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s
            LIMIT 1
            """,
            (mart_db, table, index_name),
        )
        return cur.fetchone() is not None


def _table_exists(conn, mart_db: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 AS ok
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """,
            (mart_db, table),
        )
        return cur.fetchone() is not None


def ensure_indexes(
    conn,
    mart_db: str = MART_DB,
    *,
    include_staging: bool = True,
) -> dict[str, list[str]]:
    """Add search / monthly-slide indexes on existing mart tables. Never touches az."""
    added: list[str] = []
    existed: list[str] = []
    skipped: list[str] = []
    db = quote_ident(mart_db)
    for table, name, cols in MART_INDEXES:
        label = f"{table}.{name}"
        if not include_staging and table in STAGING_INDEX_TABLES:
            skipped.append(label)
            continue
        if not _table_exists(conn, mart_db, table):
            skipped.append(label)
            continue
        if index_exists(conn, mart_db, table, name):
            existed.append(label)
            continue
        print(f"phase6 index {label}", flush=True)
        with conn.cursor() as cur:
            try:
                cur.execute(
                    f"ALTER TABLE {db}.{quote_ident(table)} ADD INDEX {quote_ident(name)} ({cols})"
                )
                conn.commit()
                added.append(label)
            except Exception as exc:
                code = exc.args[0] if getattr(exc, "args", None) else None
                if code == 1061:
                    conn.rollback()
                    existed.append(label)
                else:
                    raise
    return {"added": added, "existed": existed, "skipped": skipped}


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


def convert_engine(conn, mart_db: str = MART_DB) -> None:
    """Move any existing mart table off InnoDB onto MyISAM. CREATE ... IF NOT EXISTS won't."""
    db = quote_ident(mart_db)
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(
                """
                SELECT ENGINE AS engine
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """,
                (mart_db, table),
            )
            row = cur.fetchone()
            if not row or (row["engine"] or "").lower() == MART_ENGINE.lower():
                continue
            print(f"engine {table}: {row['engine']} -> {MART_ENGINE}", flush=True)
            cur.execute(f"ALTER TABLE {db}.{quote_ident(table)} ENGINE={MART_ENGINE}")


def create_schema(conn, mart_db: str = MART_DB) -> None:
    from provider_directory.db import ensure_mart_database

    ensure_mart_database(conn, mart_db)
    with conn.cursor() as cur:
        for stmt in ddl_statements(mart_db):
            cur.execute(stmt)
    migrate_phase2_columns(conn, mart_db)
    migrate_phase3_columns(conn, mart_db)
    migrate_phase4_columns(conn, mart_db)
    migrate_phase5_columns(conn, mart_db)
    migrate_extras_columns(conn, mart_db)
    migrate_reference_columns(conn, mart_db)
    migrate_group_summary_columns(conn, mart_db)
    convert_engine(conn, mart_db)
    convert_persistent_collation(conn, mart_db)
