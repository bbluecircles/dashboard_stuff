"""Refresh clocks. Never phase1. Never scan pat_dt from the UI.

Clocks, one mart per --state:

  claims         warehouse grew a usable month → phase6 --slide, then E/M + POS extras
  spine          Type 1 NPIs on {st}.physician missing from pd_provider (hung on claims)
  cms            new DAC/NPPES already in data/cms → overlay-cms (+ optional --reload-pdc)
  open_payments  new Open Payments CSVs → extras --download (reuses cache; general file is huge)
  mips / util    Care Compare yearly files → extras --download --skip-open-payments
"""

from __future__ import annotations

from provider_directory.db import ensure_mart_database
from provider_directory.mart import overlay_cms
from provider_directory.pipeline import run_extras, run_phase6
from provider_directory.refresh import _window_plan
from provider_directory.settings import CLAIMS_DB, LOOKUP_DB, MARKET_STATE, MART_DB
from provider_directory.spine import upsert_spine


def run_sync(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
    market_state: str = MARKET_STATE,
    claims: bool = True,
    spine: bool = False,
    cms: bool = False,
    open_payments: bool = False,
    mips: bool = False,
    utilization: bool = False,
    reload_pdc: bool = False,
    skip_staging_indexes: bool = False,
    dry_run: bool = False,
) -> dict:
    """Apply the requested refresh clocks. Does not call run_phase1 or rebuild_spine."""
    if claims:
        spine = True
    if not dry_run:
        ensure_mart_database(conn, mart_db)
    plan = _window_plan(conn, mart_db, claims_db)
    summary: dict = {
        "state": market_state,
        "mart_db": mart_db,
        "claims_db": claims_db,
        "lookup_db": lookup_db,
        "dry_run": dry_run,
        "clocks": {
            "claims": claims,
            "spine": spine,
            "cms": cms,
            "open_payments": open_payments,
            "mips": mips,
            "utilization": utilization,
        },
        "plan": plan,
        "ran": [],
        "skipped": [],
        "phase1": False,
        "pat_dt": False,
    }
    inserted = 0
    if spine:
        if dry_run:
            summary["spine_action"] = "would_upsert"
            summary["ran"].append("spine: would upsert Type 1 NPIs")
        else:
            spine_out = upsert_spine(
                conn,
                mart_db=mart_db,
                claims_db=claims_db,
                lookup_db=lookup_db,
            )
            inserted = int(spine_out.get("inserted") or 0)
            summary["spine"] = spine_out
            summary["spine_action"] = "upsert"
            summary["ran"].append(f"spine upsert inserted={inserted}")
    else:
        summary["spine_action"] = "not_requested"

    overlay_done = False
    if cms:
        if dry_run:
            summary["ran"].append("cms: would overlay-cms")
            if reload_pdc:
                summary["ran"].append("cms: would extras --reload-pdc")
        else:
            overlay_cms(conn, mart_db=mart_db, market_state=market_state)
            overlay_done = True
            summary["ran"].append("overlay-cms")
            summary["cms"] = {"overlay": True, "reload_pdc": reload_pdc}
    elif reload_pdc:
        summary["skipped"].append("--reload-pdc ignored without --cms")

    if inserted > 0 and not overlay_done and not dry_run:
        overlay_cms(conn, mart_db=mart_db, market_state=market_state)
        summary["ran"].append("overlay-cms (new spine NPIs)")
        summary["cms"] = {"overlay": True, "reload_pdc": False, "reason": "new_spine_npis"}

    slid = False
    if claims:
        if not plan["slide_available"]:
            summary["skipped"].append("claims: no new usable month (2-month lag already applied)")
            summary["claims_action"] = "noop"
        elif dry_run:
            summary["claims_action"] = "would_slide"
            summary["ran"].append("claims: would phase6 --slide")
        else:
            summary["phase6"] = run_phase6(
                conn,
                mart_db=mart_db,
                claims_db=claims_db,
                lookup_db=lookup_db,
                market_state=market_state,
                slide=True,
                skip_staging_indexes=skip_staging_indexes,
            )
            slid = True
            summary["claims_action"] = "slide"
            summary["ran"].append("phase6 --slide")
    else:
        summary["claims_action"] = "not_requested"

    need_extras = slid or cms or open_payments or mips or utilization
    if not need_extras:
        if dry_run and claims and plan["slide_available"]:
            summary["ran"].append("extras: would refresh E/M and POS after slide")
        return summary

    extras_kwargs = {
        "mart_db": mart_db,
        "claims_db": claims_db,
        "lookup_db": lookup_db,
        "market_state": market_state,
        "download": bool(open_payments or mips or utilization),
        "reload_pdc": bool(cms and reload_pdc),
        "skip_mips": not mips,
        "skip_utilization": not utilization,
        "skip_open_payments": not open_payments,
    }
    if dry_run:
        summary["extras"] = {"would_run": extras_kwargs}
        summary["ran"].append("extras: would run with the flags above")
        return summary

    summary["extras"] = run_extras(conn, **extras_kwargs)
    summary["ran"].append("extras")
    return summary
