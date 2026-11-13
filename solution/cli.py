"""Command line: ``ingest`` a drop, then ``report`` from the database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.db import connect, init_db
from solution.ingest import ingest_drop
from solution.reference import default_schemas_dir, load_dispositions, load_schemas
from solution.reports import write_reports

DEFAULT_DB = "au_assignment.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m solution",
        description="Ingest campaign drops and write the interaction-ledger reports.",
    )
    db_help = f"SQLite file (default: {DEFAULT_DB} in the working directory)"
    schema_help = "Directory with workflow JSON and dispositions.csv"
    parser.add_argument("--db", default=DEFAULT_DB, help=db_help)
    parser.add_argument("--schemas", default=None, help=schema_help)
    sub = parser.add_subparsers(dest="command", required=True)

    # default=SUPPRESS so a value passed before the subcommand is kept.
    ingest = sub.add_parser("ingest", help="Load one drop folder into the ledger")
    ingest.add_argument("drop", help="Path to a drop folder, for example data/drop_1")
    ingest.add_argument("--db", default=argparse.SUPPRESS, help=db_help)
    ingest.add_argument("--schemas", default=argparse.SUPPRESS, help=schema_help)

    report = sub.add_parser("report", help="Write the five report files from the ledger")
    report.add_argument("--out", required=True, help="Directory to write the report files into")
    report.add_argument("--db", default=argparse.SUPPRESS, help=db_help)
    report.add_argument("--schemas", default=argparse.SUPPRESS, help=schema_help)

    args = parser.parse_args(argv)
    schemas_dir = Path(args.schemas) if args.schemas else default_schemas_dir()
    try:
        schemas = load_schemas(schemas_dir)
        catalogue = load_dispositions(schemas_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    db_path = Path(args.db)
    conn = connect(db_path)
    try:
        init_db(conn)
        if args.command == "ingest":
            stats = ingest_drop(conn, Path(args.drop), schemas, catalogue)
            print(
                f"Ingested {args.drop} into {db_path}: "
                f"{stats.files_read} file(s) read, {stats.files_skipped} already ingested"
            )
            return 0
        out = Path(args.out)
        write_reports(conn, out, catalogue)
        print(f"Wrote reports to {out}")
        return 0
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
