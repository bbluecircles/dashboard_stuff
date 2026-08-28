"""CLI for Phase 1. FastAPI will call the same functions, not this module.

Examples:
  python -m provider_directory.cli init-schema
  python -m provider_directory.cli phase1
  python -m provider_directory.cli phase1 --download --skip-nppes
  python -m provider_directory.cli get 1234567893
"""

from __future__ import annotations

import argparse
import json
import sys

from provider_directory.db import ConfigError, get_connection
from provider_directory.lookup import get_provider, search_providers
from provider_directory.pipeline import download_cms_files, run_phase1
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


def _cmd_overlay(_args: argparse.Namespace) -> int:
    with get_connection() as conn:
        overlay_cms(conn)
    print("Overlaid CMS identity onto pd_provider.")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    with get_connection() as conn:
        if args.last_name or args.specialty:
            result = search_providers(
                conn, last_name=args.last_name, npi=args.npi, specialty=args.specialty, limit=args.limit
            )
            print(result.model_dump_json(indent=2))
            return 0
        if args.npi is None:
            print("Pass an NPI or --last-name / --specialty.", file=sys.stderr)
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

    p = sub.add_parser("get", help="Look up a provider (preview of the future API)")
    p.add_argument("npi", nargs="?", type=int)
    p.add_argument("--last-name")
    p.add_argument("--specialty")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=_cmd_get)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
