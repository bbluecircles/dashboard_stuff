"""Locked Phase 0 constants plus connection/cache env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
if load_dotenv is not None:
    load_dotenv(ROOT / ".env")

CLAIMS_DB = os.environ.get("PD_CLAIMS_DB", "az")
LOOKUP_DB = os.environ.get("PD_LOOKUP_DB", "azal")
MART_DB = os.environ.get("PD_MART_DB", "az_pd")
DEFAULT_MARKET_STATE = os.environ.get("PD_MARKET_STATE", "AZ").strip().upper() or "AZ"
MART_CHARSET = os.environ.get("PD_MART_CHARSET", "utf8mb4")
MART_COLLATION = os.environ.get("PD_MART_COLLATION", "utf8mb4_unicode_520_ci")

WINDOW_START = int(os.environ.get("PD_WINDOW_START", "202308"))
WINDOW_END = int(os.environ.get("PD_WINDOW_END", "202407"))
WINDOW_MONTHS = int(os.environ.get("PD_WINDOW_MONTHS", "12"))
WINDOW_LAG_MONTHS = int(os.environ.get("PD_WINDOW_LAG_MONTHS", "2"))
PRIOR_WINDOW_START = int(os.environ.get("PD_PRIOR_WINDOW_START", str(WINDOW_START - 100)))
PRIOR_WINDOW_END = int(os.environ.get("PD_PRIOR_WINDOW_END", str(WINDOW_END - 100)))
REPORT_YEAR = 2024
GRAD_AGE_OFFSET = 26
MIN_GRAD_YEAR = 1950
MAX_ESTIMATED_AGE = 90

TYPE1_CODE = "1"
DUMMY_NPIS = frozenset({0, 4})
DUMMY_STATES = frozenset({"XX"})
NPI_MIN = 1_000_000_000
NPI_MAX = 9_999_999_999
MARKET_STATE = DEFAULT_MARKET_STATE if re.fullmatch(r"[A-Z]{2}", DEFAULT_MARKET_STATE) else "AZ"
MAX_PRACTICE_SITES = 5
MAX_REFERRAL_PEERS = 3
GEO_CLUSTER_DECIMALS = 4
REFERRAL_IN = "in"
REFERRAL_OUT = "out"

PAYOR_MEDICARE_FFS = 1
PAYOR_MEDICAID = 2
PAYOR_COMMERCIAL = 3
PAYOR_HMO_MA = 4
PAYOR_OTHER = 5
PAYOR_MIX_CODES = (PAYOR_MEDICARE_FFS, PAYOR_MEDICAID, PAYOR_COMMERCIAL, PAYOR_HMO_MA)

CMS_CACHE_DIR = Path(os.environ.get("PD_CMS_CACHE", str(ROOT / "data" / "cms")))
API_JOB_STORE = Path(os.environ.get("PD_API_JOB_STORE", str(ROOT / "data" / "api_jobs.json")))
API_HOST = os.environ.get("PD_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("PD_API_PORT", "8080"))
SEARCH_LIMIT_MAX = 500
DUMP_PAGE_DEFAULT = 50

PDC_CLINICIAN_DATASET = "mj5m-pzi6"
PDC_FACILITY_DATASET = "27ea-46a8"
PDC_MIPS_DATASET = "a174-a962"
PDC_UTILIZATION_DATASET = "n0yb-util"
PDC_METASTORE = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/{dataset_id}"
)
NPPES_INDEX_URL = "https://download.cms.gov/nppes/NPI_Files.html"
OPEN_PAYMENTS_DATASETS = "https://openpaymentsdata.cms.gov/api/1/metastore/schemas/dataset/items"
MAX_UTILIZATION_CATEGORIES = 5
NEW_PATIENT_PX = ("99202", "99203", "99204", "99205")
ESTABLISHED_PX = ("99212", "99213", "99214", "99215")
POS_OFFICE = (11,)
POS_HOPD = (19, 22)
POS_ASC = (24,)
POS_ED = (23,)
POS_TELEHEALTH = (2, 10)
POS_INPATIENT = (21,)
POS_LAB = (81,)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_STATE_RE = re.compile(r"^[A-Z]{2}$")


def require_ident(name: str, label: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid {label}: {name!r}")
    return name


@dataclass(frozen=True)
class Market:
    """One warehouse + mart per USPS state. AZ → az / azal / az_pd."""

    state: str
    claims_db: str
    lookup_db: str
    mart_db: str


def parse_state(raw: str | None) -> str:
    text = (raw or MARKET_STATE).strip().upper()
    if not _STATE_RE.fullmatch(text):
        raise ValueError(f"Invalid state {raw!r}; use a two-letter USPS code like AZ")
    return text


def market_for_state(state: str | None = None) -> Market:
    st = parse_state(state)
    code = st.lower()
    return Market(
        state=st,
        claims_db=require_ident(code, "claims database"),
        lookup_db=require_ident(f"{code}al", "lookup database"),
        mart_db=require_ident(f"{code}_pd", "mart database"),
    )
