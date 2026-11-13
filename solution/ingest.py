"""Read one drop folder into the ledger. Re-reading a file is a no-op."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from solution.db import add_quality, init_db
from solution.events import (
    event_exists,
    ingest_event_object,
    link_unresolved,
    note_duplicate,
    note_event_line,
    note_malformed,
)
from solution.leads import RejectedRow, evaluate_row, is_blank_row, map_row, store_lead
from solution.reference import Disposition, Schema


@dataclass(frozen=True)
class IngestStats:
    files_read: int
    files_skipped: int


def ingest_drop(
    conn: sqlite3.Connection,
    drop: Path,
    schemas: dict[str, Schema],
    catalogue: dict[tuple[str, str], Disposition],
) -> IngestStats:
    drop = drop.resolve()
    if not drop.is_dir():
        raise FileNotFoundError(f"Drop folder not found: {drop}")
    init_db(conn)
    lead_files = _lead_files(drop)
    event_files = _event_files(drop)
    read = 0
    skipped = 0
    conn.execute("BEGIN")
    try:
        for path in lead_files:
            if _already_ingested(conn, drop, path):
                skipped += 1
                continue
            _ingest_lead_file(conn, path, schemas)
            _remember_file(conn, drop, path)
            read += 1
        for path in event_files:
            if _already_ingested(conn, drop, path):
                skipped += 1
                continue
            _ingest_event_file(conn, path, catalogue)
            _remember_file(conn, drop, path)
            read += 1
        link_unresolved(conn, catalogue)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return IngestStats(files_read=read, files_skipped=skipped)


def _lead_files(drop: Path) -> list[Path]:
    root = drop / "leads"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*/*.csv") if path.is_file())


def _event_files(drop: Path) -> list[Path]:
    root = drop / "events"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.jsonl") if path.is_file())


def _relative(drop: Path, path: Path) -> str:
    return path.resolve().relative_to(drop).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _already_ingested(conn: sqlite3.Connection, drop: Path, path: Path) -> bool:
    relative = _relative(drop, path)
    row = conn.execute(
        "SELECT sha256 FROM ingest_files WHERE drop_path = ? AND relative_path = ?",
        (str(drop), relative),
    ).fetchone()
    if row is None:
        return False
    current = _sha256(path)
    if row["sha256"] != current:
        raise RuntimeError(
            f"{relative} changed after it was ingested. Refusing to reprocess it, "
            "because that would double-count rows."
        )
    return True


def _remember_file(conn: sqlite3.Connection, drop: Path, path: Path) -> None:
    conn.execute(
        "INSERT INTO ingest_files (drop_path, relative_path, sha256) VALUES (?, ?, ?)",
        (str(drop), _relative(drop, path), _sha256(path)),
    )


def _ingest_lead_file(
    conn: sqlite3.Connection,
    path: Path,
    schemas: dict[str, Schema],
) -> None:
    workflow_id = path.parent.name
    schema = schemas.get(workflow_id)
    if schema is None:
        raise RuntimeError(f"No schema for workflow '{workflow_id}' ({path})")
    batch_id = path.stem
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        row_number = 0
        for csv_row in reader:
            if is_blank_row(csv_row):
                continue
            row_number += 1
            add_quality(conn, lead_rows_total=1)
            outcome = evaluate_row(schema, map_row(schema, csv_row))
            if isinstance(outcome, RejectedRow):
                add_quality(conn, lead_rows_rejected=1)
                conn.execute(
                    """
                    INSERT INTO rejections (workflow_id, batch_id, row_number, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (workflow_id, batch_id, row_number, outcome.reason),
                )
                continue
            if outcome.warnings:
                add_quality(conn, field_warnings=outcome.warnings)
            store_lead(conn, schema, batch_id, outcome)


def _ingest_event_file(
    conn: sqlite3.Connection,
    path: Path,
    catalogue: dict[tuple[str, str], Disposition],
) -> None:
    text = path.read_text(encoding="utf-8-sig")
    for line in text.splitlines():
        if line == "":
            continue
        note_event_line(conn)
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            note_malformed(conn)
            continue
        if not isinstance(obj, dict) or not _has_event_id(obj):
            note_malformed(conn)
            continue
        event_id = str(obj["event_id"]).strip()
        if event_exists(conn, event_id):
            note_duplicate(conn)
            continue
        ingest_event_object(conn, obj, catalogue)


def _has_event_id(obj: dict) -> bool:
    if "event_id" not in obj or obj["event_id"] is None:
        return False
    return str(obj["event_id"]).strip() != ""
