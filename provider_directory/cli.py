"""CLI. FastAPI will call the same functions, not this module.

Examples:
  python -m provider_directory.cli phase1
  python -m provider_directory.cli phase1 --download --skip-nppes
  python -m provider_directory.cli phase2
  python -m provider_directory.cli phase3
  python -m provider_directory.cli phase4
  python -m provider_directory.cli phase5
  python -m provider_directory.cli phase6
  python -m provider_directory.cli serve
  python -m provider_directory.cli get --last-name Smith --limit 3
"""

from __future__ import annotations

import argparse
import json
import sys

from provider_directory.db import ConfigError, get_connection
from provider_directory.locations import Phase2Required
from provider_directory.lookup import get_provider, search_providers
from provider_directory.pipeline import download_cms_files, run_phase1, run_phase2, run_phase3, run_phase4, run_phase5, run_phase6
from provider_directory.schema import create_schema
from provider_directory.spine import rebuild_spine
from provider_directory.mart import overlay_cms


def _cmd_init_schema(_args: argparse.Namespace) -> int:
    with get_connection() as conn:
        create_schema(conn)
    print("Created mart schema (cms_pdc_*, pd_provider, pd_npi_xwalk, pd_network_npi).")
    return 0


def _cmd_build_spine(_args: argparse.Namespace) -> int:
    with get_connection() as conn:
        n = rebuild_spine(conn)
    print(f"Loaded {n} Type 1 NPIs into pd_provider.")
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
        )
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_phase2(_args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase2(conn)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase3(_args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase3(conn)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase4(_args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase4(conn)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase5(_args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase5(conn)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_phase6(args: argparse.Namespace) -> int:
    with get_connection(autocommit=False) as conn:
        summary = run_phase6(
            conn,
            slide=args.slide,
            skip_staging_indexes=args.skip_staging_indexes,
        )
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from provider_directory.api import serve

    serve(host=args.host, port=args.port)
    return 0


def _cmd_overlay(_args: argparse.Namespace) -> int:
    with get_connection() as conn:
        overlay_cms(conn)
    print("Overlaid CMS identity onto pd_provider.")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    with get_connection() as conn:
        searching = any(
            [
                args.last_name,
                args.specialty,
                args.active,
                args.min_visits is not None,
            ]
        )
        if searching:
            result = search_providers(
                conn,
                last_name=args.last_name,
                npi=args.npi,
                specialty=args.specialty,
                active=True if args.active else None,
                min_visits=args.min_visits,
                limit=args.limit,
            )
            print(result.model_dump_json(indent=2))
            return 0
        if args.npi is None:
            print("Pass an NPI or --last-name / --specialty / --active.", file=sys.stderr)
            return 2
        row = get_provider(conn, args.npi)
    if row is None:
        print(f"NPI {args.npi} not in pd_provider.", file=sys.stderr)
        return 1
    print(row.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="provider_directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-schema", help="Create az_pd tables")
    p.set_defaults(func=_cmd_init_schema)

    p = sub.add_parser("build-spine", help="Rebuild pd_provider from az.physician")
    p.set_defaults(func=_cmd_build_spine)

    p = sub.add_parser("download-cms", help="Download PDC/NPPES files into data/cms")
    p.add_argument("--skip-pdc", action="store_true")
    p.add_argument("--skip-nppes", action="store_true")
    p.set_defaults(func=_cmd_download)

    p = sub.add_parser("overlay-cms", help="Fill gender/school/age from cms_* tables")
    p.set_defaults(func=_cmd_overlay)

    p = sub.add_parser("phase1", help="Schema + spine + optional CMS load + overlay")
    p.add_argument("--download", action="store_true", help="Fetch CMS files before loading")
    p.add_argument("--skip-pdc", action="store_true")
    p.add_argument("--skip-nppes", action="store_true")
    p.set_defaults(func=_cmd_phase1)

    p = sub.add_parser(
        "phase2",
        help="Activity + panel + top dx/px for period_code 202308–202407",
    )
    p.set_defaults(func=_cmd_phase2)

    p = sub.add_parser(
        "phase3",
        help="Rank 5 claims-weighted practice sites from Phase 2 staging; overlay PDC phones",
    )
    p.set_defaults(func=_cmd_phase3)

    p = sub.add_parser(
        "phase4",
        help="wRVU, payer mix, primary org, and work_type polish for 202308–202407",
    )
    p.set_defaults(func=_cmd_phase4)

    p = sub.add_parser(
        "phase5",
        help="Referrals both ways, day-of-week mix, prior-year wRVU, state-specialty benchmarks",
    )
    p.set_defaults(func=_cmd_phase5)

    p = sub.add_parser(
        "phase6",
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
        "serve",
        help="HTTP API for the .NET app (lookup + background phase jobs). Bind 127.0.0.1 and run under NSSM.",
    )
    p.add_argument("--host", default=None, help="Default PD_API_HOST or 127.0.0.1")
    p.add_argument("--port", type=int, default=None, help="Default PD_API_PORT or 8080")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("get", help="Look up a provider (preview of the future API)")
    p.add_argument("npi", nargs="?", type=int)
    p.add_argument("--last-name")
    p.add_argument("--specialty")
    p.add_argument("--active", action="store_true", help="Only providers with activity in the frozen window")
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
