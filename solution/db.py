"""SQLite schema for the interaction ledger.

Events are inserted once. A later drop may fill ``lead_id`` on a row that
arrived before its lead, and may set ``ignored`` when a now-linked disposition
code is not in the catalogue. Event payloads are never rewritten, and a
``disposition.set`` row is never edited into an override.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    phone_hmac TEXT NOT NULL,
    phone_last4 TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    external_ref TEXT,
    fields_json TEXT NOT NULL,
    name_ciphertext TEXT,
    ingest_seq INTEGER NOT NULL,
    UNIQUE (workflow_id, phone_hmac)
);

CREATE TABLE IF NOT EXISTS rejections (
    id INTEGER PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    workflow_id TEXT,
    call_id TEXT,
    occurred_at_utc TEXT,
    phone_hmac TEXT,
    payload_json TEXT NOT NULL,
    lead_id INTEGER REFERENCES leads(id),
    ignored INTEGER NOT NULL DEFAULT 0,
    ingest_seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_files (
    drop_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (drop_path, relative_path)
);

CREATE TABLE IF NOT EXISTS quality (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    lead_rows_total INTEGER NOT NULL DEFAULT 0,
    lead_rows_rejected INTEGER NOT NULL DEFAULT 0,
    duplicate_lead_rows_merged INTEGER NOT NULL DEFAULT 0,
    field_warnings INTEGER NOT NULL DEFAULT 0,
    events_total INTEGER NOT NULL DEFAULT 0,
    malformed_events INTEGER NOT NULL DEFAULT 0,
    duplicate_events_dropped INTEGER NOT NULL DEFAULT 0,
    next_seq INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_events_lead ON events(lead_id);
CREATE INDEX IF NOT EXISTS idx_events_call ON events(workflow_id, call_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO quality (id) VALUES (1)")


def add_quality(conn: sqlite3.Connection, **deltas: int) -> None:
    if not deltas:
        return
    assignments = ", ".join(f"{column} = {column} + ?" for column in deltas)
    conn.execute(
        f"UPDATE quality SET {assignments} WHERE id = 1",
        tuple(deltas.values()),
    )


def allocate_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT next_seq FROM quality WHERE id = 1").fetchone()
    current = int(row["next_seq"])
    conn.execute("UPDATE quality SET next_seq = next_seq + 1 WHERE id = 1")
    return current
