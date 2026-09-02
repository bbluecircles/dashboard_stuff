"""Locked Phase 0 constants plus connection/cache env."""

from __future__ import annotations

import os
import re
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
MARKET_STATE = "AZ"
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

PDC_CLINICIAN_DATASET = "mj5m-pzi6"
PDC_FACILITY_DATASET = "27ea-46a8"
PDC_METASTORE = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/{dataset_id}"
)
NPPES_INDEX_URL = "https://download.cms.gov/nppes/NPI_Files.html"

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def require_ident(name: str, label: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid {label}: {name!r}")
    return name
