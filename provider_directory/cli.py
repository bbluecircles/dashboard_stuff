"""CLI. FastAPI will call the same functions, not this module.

Examples:
  python -m provider_directory.cli phase1
  python -m provider_directory.cli phase1 --download --skip-nppes
  python -m provider_directory.cli phase2
  python -m provider_directory.cli phase3
  python -m provider_directory.cli phase4
  python -m provider_directory.cli phase5
  python -m provider_directory.cli phase6
  python -m provider_directory.cli extras --state AZ --skip-open-payments
  python -m provider_directory.cli get --state AZ 1952863797
  python -m provider_directory.cli extras --download
  python -m provider_directory.cli serve
  python -m provider_directory.cli get --last-name Smith --limit 3
"""

from __future__ import annotations

import argparse
import json
import sys

from provider_directory.db import ConfigError, ensure_mart_database, get_connection
from provider_directory.locations import Phase2Required
from provider_directory.lookup import get_provider, list_providers
from provider_directory.extras import OPEN_PAYMENTS_KINDS, parse_open_payments_kinds
from provider_directory.pipeline import (
    download_cms_files,
    run_extras,
    run_phase1,
    run_phase2,
    run_phase3,
    run_phase4,
    run_phase5,
    run_phase6,
)
from provider_directory.schema import create_schema
from provider_directory.settings import MARKET_STATE, Market, market_for_state, parse_state
from provider_directory.spine import rebuild_spine
from provider_directory.mart import overlay_cms


def _state_type(raw: str) -> str:
    try:
        return parse_state(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _market(args: argparse.Namespace) -> Market:
    return market_for_state(args.state)


def _market_kwargs(args: argparse.Namespace) -> dict:
    market = _market(args)
    return {
        "mart_db": market.mart_db,
        "claims_db": market.claims_db,
        "lookup_db": market.lookup_db,
        "market_state": market.state,
    }


def _cmd_init_schema(args: argparse.Namespace) -> int:
    market = _market(args)
    with get_connection() as conn:
        ensure_mart_database(conn, market.mart_db)
        create_schema(conn, market.mart_db)
    print(f"Created mart schema in {market.mart_db} ({market.state}).")
    return 0


def _cmd_build_spine(args: argparse.Namespace) -> int:
    market = _market(args)
    with get_connection() as conn:
        n = rebuild_spine(
            conn,
            mart_db=market.mart_db,
            claims_db=market.claims_db,
            lookup_db=market.lookup_db,
        )
    print(f"Loaded {n} Type 1 NPIs into {market.mart_db}.pd_provider.")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    paths = download_cms_files(skip_pdc=args.skip_pdc, skip_nppes=args.skip_nppes)
    for key, path in paths.items():
        print(f"{key}: {path} ({path.stat().st_size} bytes)")
    return 0


def _cmd_phase1(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase1(
            conn,
            download=args.download,
            skip_pdc=args.skip_pdc,
            skip_nppes=args.skip_nppes,
            **_market_kwargs(args),
        )
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_phase2(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase2(conn, **_market_kwargs(args))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase3(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase3(conn, **_market_kwargs(args))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase4(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase4(conn, **_market_kwargs(args))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase5(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase5(conn, **_market_kwargs(args))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase6(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase6(
            conn,
            slide=args.slide,
            skip_staging_indexes=args.skip_staging_indexes,
            **_market_kwargs(args),
        )
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _open_payments_kinds_type(raw: str) -> tuple[str, ...]:
    try:
        return parse_open_payments_kinds(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _cmd_extras(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_extras(
            conn,
            download=args.download,
            reload_pdc=args.reload_pdc,
            skip_mips=args.skip_mips,
            skip_utilization=args.skip_utilization,
            skip_open_payments=args.skip_open_payments,
            year=args.year,
            open_payments_kinds=args.open_payments_kinds or OPEN_PAYMENTS_KINDS,
            open_payments_overlay_only=args.open_payments_overlay_only,
            **_market_kwargs(args),
        )
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from provider_directory.api import serve

    serve(host=args.host, port=args.port)
    return 0


def _cmd_overlay(args: argparse.Namespace) -> int:
    market = _market(args)
    with get_connection() as conn:
        overlay_cms(conn, mart_db=market.mart_db, market_state=market.state)
    print(f"Overlaid CMS identity onto {market.mart_db}.pd_provider.")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    market = _market(args)
    with get_connection() as conn:
        searching = any(
            [
                args.last_name,
                args.specialty,
                args.active,
                args.min_visits is not None,
                args.in_system,
            ]
        )
        if searching:
            result = list_providers(
                conn,
                last_name=args.last_name,
                npi=args.npi,
                specialty=args.specialty,
                active=True if args.active else None,
                min_visits=args.min_visits,
                limit=args.limit,
                in_system=True if args.in_system else None,
                mart_db=market.mart_db,
                state=market.state,
            )
            print(result.model_dump_json(indent=2))
            return 0
        if args.npi is None:
            print("Pass an NPI or --last-name / --specialty / --active.", file=sys.stderr)
            return 2
        row = get_provider(conn, args.npi, mart_db=market.mart_db)
    if row is None:
        print(f"NPI {args.npi} not in {market.mart_db}.pd_provider.", file=sys.stderr)
        return 1
    print(row.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="provider_directory")
    state_parent = argparse.ArgumentParser(add_help=False)
    state_parent.add_argument(
        "--state",
        default=MARKET_STATE,
        type=_state_type,
        help="USPS state. Selects claims {st}, lookup {st}al, mart {st}_pd. Default AZ.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-schema", parents=[state_parent], help="Create {st}_pd tables")
    p.set_defaults(func=_cmd_init_schema)

    p = sub.add_parser(
        "build-spine",
        parents=[state_parent],
        help="Rebuild pd_provider from {st}.physician",
    )
    p.set_defaults(func=_cmd_build_spine)

    p = sub.add_parser(
        "download-cms",
        parents=[state_parent],
        help="Download PDC/NPPES files into data/cms (national; shared across states)",
    )
    p.add_argument("--skip-pdc", action="store_true")
    p.add_argument("--skip-nppes", action="store_true")
    p.set_defaults(func=_cmd_download)

    p = sub.add_parser(
        "overlay-cms",
        parents=[state_parent],
        help="Fill gender/school/age from cms_* tables",
    )
    p.set_defaults(func=_cmd_overlay)

    p = sub.add_parser(
        "phase1",
        parents=[state_parent],
        help="Schema + spine + optional CMS load + overlay",
    )
    p.add_argument("--download", action="store_true", help="Fetch CMS files before loading")
    p.add_argument("--skip-pdc", action="store_true")
    p.add_argument("--skip-nppes", action="store_true")
    p.set_defaults(func=_cmd_phase1)

    p = sub.add_parser(
        "phase2",
        parents=[state_parent],
        help="Activity + panel + top dx/px for period_code 202308–202407",
    )
    p.set_defaults(func=_cmd_phase2)

    p = sub.add_parser(
        "phase3",
        parents=[state_parent],
        help="Rank 5 claims-weighted practice sites from Phase 2 staging; overlay PDC phones",
    )
    p.set_defaults(func=_cmd_phase3)

    p = sub.add_parser(
        "phase4",
        parents=[state_parent],
        help="wRVU, payer mix, primary org, and work_type polish for 202308–202407",
    )
    p.set_defaults(func=_cmd_phase4)

    p = sub.add_parser(
        "phase5",
        parents=[state_parent],
        help="Referrals both ways, day-of-week mix, prior-year wRVU, state-specialty benchmarks",
    )
    p.set_defaults(func=_cmd_phase5)

    p = sub.add_parser(
        "phase6",
        parents=[state_parent],
        help="Mart indexes + window watermark; --slide adds only new months instead of a 12-month pat_dt rescan",
    )
    p.add_argument(
        "--slide",
        action="store_true",
        help="If az has a later usable month, drop the oldest month and rebuild 3–5 from updated staging",
    )
    p.add_argument(
        "--skip-staging-indexes",
        action="store_true",
        help="Only add pd_provider search indexes; skip period_code indexes on 49M-row staging",
    )
    p.set_defaults(func=_cmd_phase6)

    p = sub.add_parser(
        "extras",
        parents=[state_parent],
        help="Cheap extras overlay (group size, POS mix, MIPS, Open Payments). Does not rescan pat_dt.",
    )
    p.add_argument(
        "--download",
        action="store_true",
        help="Fetch MIPS, utilization, and Open Payments CSVs into data/cms. Reuses cached files. Open Payments general file is huge.",
    )
    p.add_argument(
        "--reload-pdc",
        action="store_true",
        help="TRUNCATE cms_pdc_clinician only and reload from data/cms so sec_spec_1–4 land. Not phase1.",
    )
    p.add_argument("--skip-mips", action="store_true")
    p.add_argument("--skip-utilization", action="store_true")
    p.add_argument("--skip-open-payments", action="store_true")
    p.add_argument(
        "--open-payments-kinds",
        type=_open_payments_kinds_type,
        default=None,
        help="Comma list: ownership,research,general. Default all. Use ownership to re-parse the 1MB file without reading the 9GB general file.",
    )
    p.add_argument("--year", type=int, default=None, help="Open Payments program year (default: latest complete year)")
    p.add_argument(
        "--open-payments-overlay-only",
        action="store_true",
        help="Rewrite pd_provider Open Payments from cms_open_payments. Does not read CSVs or rerun E/M, POS, MIPS.",
    )
    p.set_defaults(func=_cmd_extras)

    p = sub.add_parser(
        "serve",
        parents=[state_parent],
        help="HTTP API for the .NET app (lookup + background phase jobs). Bind 127.0.0.1 and run under NSSM.",
    )
    p.add_argument("--host", default=None, help="Default PD_API_HOST or 127.0.0.1")
    p.add_argument("--port", type=int, default=None, help="Default PD_API_PORT or 8080")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("get", parents=[state_parent], help="Look up a provider (preview of the future API)")
    p.add_argument("npi", nargs="?", type=int)
    p.add_argument("--last-name")
    p.add_argument("--specialty")
    p.add_argument("--active", action="store_true", help="Only providers with activity in the frozen window")
    p.add_argument(
        "--in-system",
        action="store_true",
        dest="in_system",
        help="Only NPIs with a CMS PDC facility affiliation (hospital CCN)",
    )
    p.add_argument("--min-visits", type=int, dest="min_visits", help="Minimum visits_total")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=_cmd_get)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Phase2Required as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
