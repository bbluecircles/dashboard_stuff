"""Phase 6: mart indexes, window watermark, optional monthly slide.

get/search already read az_pd only. This module keeps rebuilds from
rescanning every frozen month of pat_dt when a later warehouse period lands.
Never ALTER az / azal.
"""

from __future__ import annotations

import pymysql

from provider_directory.activity import slide_activity
from provider_directory.analytics import rebuild_analytics
from provider_directory.complete import rebuild_complete
from provider_directory.db import quote_ident
from provider_directory.locations import rebuild_locations
from provider_directory.schema import create_schema, ensure_indexes
from provider_directory.settings import (
    CLAIMS_DB,
    MART_DB,
    PRIOR_WINDOW_END,
    PRIOR_WINDOW_START,
    WINDOW_END,
    WINDOW_LAG_MONTHS,
    WINDOW_MONTHS,
    WINDOW_START,
)
from provider_directory.window import prior_window, slide_diff, usable_window

WAREHOUSE_PERIOD_SQL = (
    ("period", "period_code"),
    ("dash_physician_payor_all", "period_code"),
)


def warehouse_max_period(conn, claims_db: str = CLAIMS_DB) -> tuple[int | None, str | None]:
    claims = quote_ident(claims_db)
    with conn.cursor() as cur:
        for table, column in WAREHOUSE_PERIOD_SQL:
            sql = f"SELECT MAX({column}) AS mx FROM {claims}.{quote_ident(table)}"
            try:
                cur.execute(sql)
                row = cur.fetchone()
            except pymysql.err.ProgrammingError:
                conn.rollback()
                continue
            if row and row["mx"] is not None:
                return int(row["mx"]), f"{claims_db}.{table}.{column}"
    return None, None


def read_refresh_state(conn, mart_db: str = MART_DB) -> dict | None:
    table = f"{quote_ident(mart_db)}.pd_refresh_state"
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {table} WHERE id = 1")
            row = cur.fetchone()
    except pymysql.err.ProgrammingError:
        conn.rollback()
        return None
    return dict(row) if row else None


def resolve_window(conn, mart_db: str = MART_DB) -> tuple[int, int, int, int]:
    state = read_refresh_state(conn, mart_db)
    if state and state.get("window_start") and state.get("window_end"):
        return (
            int(state["window_start"]),
            int(state["window_end"]),
            int(state["prior_window_start"]),
            int(state["prior_window_end"]),
        )
    return WINDOW_START, WINDOW_END, PRIOR_WINDOW_START, PRIOR_WINDOW_END


def upsert_refresh_state(
    conn,
    mart_db: str = MART_DB,
    *,
    window_start: int,
    window_end: int,
    prior_window_start: int | None = None,
    prior_window_end: int | None = None,
    warehouse_max_period: int | None = None,
    warehouse_source: str | None = None,
    slide_available: int | bool | None = None,
    last_action: str | None = None,
    notes: str | None = None,
    touch_indexes: bool = False,
    touch_slide: bool = False,
) -> None:
    current = read_refresh_state(conn, mart_db) or {}
    if prior_window_start is None or prior_window_end is None:
        prior_window_start, prior_window_end = prior_window(window_start, window_end)
    if warehouse_max_period is None:
        warehouse_max_period = current.get("warehouse_max_period")
    if warehouse_source is None:
        warehouse_source = current.get("warehouse_source")
    if notes is None:
        notes = current.get("notes")
    if last_action is None:
        last_action = current.get("last_action")
    if slide_available is None:
        slide_available = current.get("slide_available", 0)
    table = f"{quote_ident(mart_db)}.pd_refresh_state"
    indexes_expr = "NOW()" if touch_indexes else "last_indexes_at"
    slide_expr = "NOW()" if touch_slide else "last_slide_at"
    sql = f"""
        INSERT INTO {table} (
            id, window_start, window_end, prior_window_start, prior_window_end,
            warehouse_max_period, warehouse_source, slide_available, last_action,
            last_indexes_at, last_slide_at, notes
        ) VALUES (
            1, %s, %s, %s, %s, %s, %s, %s, %s,
            {"NOW()" if touch_indexes else "NULL"},
            {"NOW()" if touch_slide else "NULL"},
            %s
        )
        ON DUPLICATE KEY UPDATE
            window_start = VALUES(window_start),
            window_end = VALUES(window_end),
            prior_window_start = VALUES(prior_window_start),
            prior_window_end = VALUES(prior_window_end),
            warehouse_max_period = VALUES(warehouse_max_period),
            warehouse_source = VALUES(warehouse_source),
            slide_available = VALUES(slide_available),
            last_action = VALUES(last_action),
            last_indexes_at = {indexes_expr},
            last_slide_at = {slide_expr},
            notes = VALUES(notes)
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                int(window_start),
                int(window_end),
                int(prior_window_start),
                int(prior_window_end),
                warehouse_max_period,
                warehouse_source,
                1 if slide_available else 0,
                last_action,
                notes,
            ),
        )
    conn.commit()


def _window_plan(conn, mart_db: str, claims_db: str) -> dict:
    current_start, current_end, prior_start, prior_end = resolve_window(conn, mart_db)
    warehouse_max, warehouse_source = warehouse_max_period(conn, claims_db)
    target_start = current_start
    target_end = current_end
    if warehouse_max is not None:
        target_start, target_end = usable_window(
            warehouse_max, lag_months=WINDOW_LAG_MONTHS, length=WINDOW_MONTHS
        )
    drop_periods, add_periods = slide_diff(current_start, current_end, target_start, target_end)
    slide_available = bool(drop_periods or add_periods)
    target_prior_start, target_prior_end = prior_window(target_start, target_end)
    return {
        "window_start": current_start,
        "window_end": current_end,
        "prior_window_start": prior_start,
        "prior_window_end": prior_end,
        "warehouse_max_period": warehouse_max,
        "warehouse_source": warehouse_source,
        "target_window_start": target_start,
        "target_window_end": target_end,
        "target_prior_window_start": target_prior_start,
        "target_prior_window_end": target_prior_end,
        "drop_periods": drop_periods,
        "add_periods": add_periods,
        "slide_available": slide_available,
        "get_reads_mart_only": True,
    }


def rebuild_refresh(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    slide: bool = False,
    skip_staging_indexes: bool = False,
) -> dict:
    create_schema(conn, mart_db)
    indexes = ensure_indexes(conn, mart_db, include_staging=not skip_staging_indexes)
    plan = _window_plan(conn, mart_db, claims_db)
    summary: dict = {
        "mart_db": mart_db,
        "action": "indexes",
        "indexes": indexes,
        **plan,
    }

    if not slide:
        upsert_refresh_state(
            conn,
            mart_db,
            window_start=plan["window_start"],
            window_end=plan["window_end"],
            prior_window_start=plan["prior_window_start"],
            prior_window_end=plan["prior_window_end"],
            warehouse_max_period=plan["warehouse_max_period"],
            warehouse_source=plan["warehouse_source"],
            slide_available=plan["slide_available"],
            last_action="indexes",
            notes="get/search read az_pd only; never ALTER az",
            touch_indexes=True,
        )
        return summary

    if not plan["slide_available"]:
        upsert_refresh_state(
            conn,
            mart_db,
            window_start=plan["window_start"],
            window_end=plan["window_end"],
            prior_window_start=plan["prior_window_start"],
            prior_window_end=plan["prior_window_end"],
            warehouse_max_period=plan["warehouse_max_period"],
            warehouse_source=plan["warehouse_source"],
            slide_available=False,
            last_action="indexes",
            notes="warehouse usable window already matches the mart",
            touch_indexes=True,
        )
        summary["action"] = "noop"
        summary["notes"] = "no new usable month; indexes applied"
        return summary

    target_start = plan["target_window_start"]
    target_end = plan["target_window_end"]
    target_prior_start = plan["target_prior_window_start"]
    target_prior_end = plan["target_prior_window_end"]

    summary["action"] = "slide"
    summary["activity"] = slide_activity(
        conn,
        drop_periods=plan["drop_periods"],
        add_periods=plan["add_periods"],
        window_start=target_start,
        window_end=target_end,
        mart_db=mart_db,
        claims_db=claims_db,
    )
    summary["locations"] = rebuild_locations(
        conn, mart_db=mart_db, claims_db=claims_db, reuse_visit_site=False
    )
    summary["analytics"] = rebuild_analytics(
        conn, mart_db=mart_db, claims_db=claims_db, window_start=target_start, window_end=target_end
    )
    summary["complete"] = rebuild_complete(
        conn,
        mart_db=mart_db,
        claims_db=claims_db,
        window_start=target_start,
        window_end=target_end,
        prior_start=target_prior_start,
        prior_end=target_prior_end,
        reuse_cached=False,
    )
    upsert_refresh_state(
        conn,
        mart_db,
        window_start=target_start,
        window_end=target_end,
        prior_window_start=target_prior_start,
        prior_window_end=target_prior_end,
        warehouse_max_period=plan["warehouse_max_period"],
        warehouse_source=plan["warehouse_source"],
        slide_available=False,
        last_action="slide",
        notes="slid window; later phase2-5 use pd_refresh_state",
        touch_indexes=True,
        touch_slide=True,
    )
    summary["window_start"] = target_start
    summary["window_end"] = target_end
    summary["prior_window_start"] = target_prior_start
    summary["prior_window_end"] = target_prior_end
    summary["slide_available"] = False
    return summary
