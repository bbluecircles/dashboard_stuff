"""Precomputed group-practice dump: one row per billing org per member scope.

list_group_practices used to aggregate every pd_provider row and rank every
group on each page request, twice (count + page). This table holds that
result so the dump and the group profile are indexed reads. Rebuilt at the
end of Phase 2, Phase 4, overlay-cms and Phase 6; the lookup falls back to
the live aggregation while the table is empty. Reads the mart only.
"""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.lookup import GROUP_SCOPES, GROUP_SUMMARY_TABLE, group_scored_sql
from provider_directory.schema import create_schema
from provider_directory.settings import MART_DB

GROUP_SUMMARY_COLUMNS = (
    "organization_id",
    "organization_name",
    "organization_npi",
    "parent_id",
    "parent_name",
    "provider_count",
    "active_provider_count",
    "in_system_provider_count",
    "visits_total",
    "panel_size",
    "wrvu_total",
    "visits_per_provider",
    "visits_percentile",
    "activity_percentile",
)


def group_summary_insert_sql(mart_db: str, scope: str) -> str:
    if scope not in GROUP_SCOPES:
        raise ValueError(f"scope must be one of {GROUP_SCOPES}, got {scope!r}")
    mart = quote_ident(mart_db)
    cols = ", ".join(GROUP_SUMMARY_COLUMNS)
    picks = ", ".join(f"s.{c}" for c in GROUP_SUMMARY_COLUMNS)
    return f"""
        INSERT INTO {mart}.{GROUP_SUMMARY_TABLE} (member_scope, {cols}, refreshed_at)
        SELECT '{scope}', {picks}, NOW()
        FROM ({group_scored_sql(mart_db, scope)}) s
    """


def rebuild_group_summary(conn, mart_db: str = MART_DB) -> dict[str, int]:
    """TRUNCATE pd_group_practice and refill all three member scopes."""
    create_schema(conn, mart_db)
    mart = quote_ident(mart_db)
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {mart}.{GROUP_SUMMARY_TABLE}")
        conn.commit()
        for scope in GROUP_SCOPES:
            cur.execute(group_summary_insert_sql(mart_db, scope))
            counts[scope] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            conn.commit()
            print(f"group summary {scope}: {counts[scope]} rows", flush=True)
    return counts
