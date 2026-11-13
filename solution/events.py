"""Append-only event ledger: dedupe, link, and resolve late leads."""

from __future__ import annotations

import json
import sqlite3

from solution.db import add_quality, allocate_seq
from solution.normalize import canonical_phone, format_utc, parse_occurred_at
from solution.pii import phone_hmac
from solution.reference import Disposition


def payload_code(payload_json: str) -> str | None:
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("disposition_code")
    if code is None:
        return None
    text = str(code).strip()
    return text or None


def is_known_disposition(
    catalogue: dict[tuple[str, str], Disposition],
    workflow_id: str | None,
    event_type: str,
    payload_json: str,
) -> bool:
    if not event_type.startswith("disposition."):
        return True
    code = payload_code(payload_json)
    if not workflow_id or not code:
        return False
    return (workflow_id, code) in catalogue


def find_lead_id(
    conn: sqlite3.Connection,
    workflow_id: str | None,
    digest: str | None,
) -> int | None:
    if not workflow_id or not digest:
        return None
    row = conn.execute(
        "SELECT id FROM leads WHERE workflow_id = ? AND phone_hmac = ?",
        (workflow_id, digest),
    ).fetchone()
    return int(row["id"]) if row else None


def find_sibling_lead(
    conn: sqlite3.Connection,
    workflow_id: str | None,
    call_id: str | None,
    event_id: str,
) -> int | None:
    """Lead already attached to another event of this call.

    ``call.initiated`` wins when siblings disagree, then the earliest event.
    """
    if not workflow_id or not call_id:
        return None
    row = conn.execute(
        """
        SELECT lead_id FROM events
        WHERE workflow_id = ? AND call_id = ? AND event_id != ?
          AND lead_id IS NOT NULL
        ORDER BY CASE event_type WHEN 'call.initiated' THEN 0 ELSE 1 END,
                 ingest_seq
        LIMIT 1
        """,
        (workflow_id, call_id, event_id),
    ).fetchone()
    return int(row["lead_id"]) if row else None


def resolve_lead_id(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    workflow_id: str | None,
    call_id: str | None,
    digest: str | None,
) -> int | None:
    by_phone = find_lead_id(conn, workflow_id, digest)
    if by_phone is not None:
        return by_phone
    if event_type == "disposition.override":
        return find_sibling_lead(conn, workflow_id, call_id, event_id)
    return None


def ingest_event_object(
    conn: sqlite3.Connection,
    obj: dict,
    catalogue: dict[tuple[str, str], Disposition],
) -> None:
    """Insert one parsed event. The caller has already counted and deduped it."""
    event_id = str(obj["event_id"]).strip()
    event_type = str(obj.get("event_type") or "")
    workflow_id = obj.get("workflow_id")
    workflow_id = str(workflow_id) if workflow_id not in (None, "") else None
    call_raw = obj.get("call_id")
    call_id = str(call_raw) if call_raw not in (None, "") else None
    occurred = parse_occurred_at(obj.get("occurred_at"))
    occurred_text = format_utc(occurred) if occurred else None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    digest = None
    phone_raw = obj.get("phone")
    if phone_raw is not None and str(phone_raw).strip():
        canonical = canonical_phone(str(phone_raw))
        if canonical:
            digest = phone_hmac(canonical)

    lead_id = resolve_lead_id(
        conn,
        event_id=event_id,
        event_type=event_type,
        workflow_id=workflow_id,
        call_id=call_id,
        digest=digest,
    )
    ignored = 0
    if lead_id is not None and not is_known_disposition(
        catalogue, workflow_id, event_type, payload_json
    ):
        ignored = 1

    conn.execute(
        """
        INSERT INTO events (
            event_id, event_type, workflow_id, call_id, occurred_at_utc,
            phone_hmac, payload_json, lead_id, ignored, ingest_seq
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            workflow_id,
            call_id,
            occurred_text,
            digest,
            payload_json,
            lead_id,
            ignored,
            allocate_seq(conn),
        ),
    )


def link_unresolved(
    conn: sqlite3.Connection,
    catalogue: dict[tuple[str, str], Disposition],
) -> None:
    """Attach events that arrived before their lead, then overrides without a phone.

    Phone links run first so an override can see the sibling ``call.initiated``
    that was stored earlier in the same file but only just became linkable.
    An event that is still unlinked stays an orphan, even if its disposition
    code is unknown. The spec checks "orphan" before "unknown disposition".
    """
    pending = conn.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id, phone_hmac, payload_json
        FROM events
        WHERE lead_id IS NULL
        ORDER BY ingest_seq
        """
    ).fetchall()
    for row in pending:
        if not row["phone_hmac"]:
            continue
        lead_id = find_lead_id(conn, row["workflow_id"], row["phone_hmac"])
        if lead_id is not None:
            _attach(conn, catalogue, row, lead_id)

    overrides = conn.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id, phone_hmac, payload_json
        FROM events
        WHERE lead_id IS NULL AND event_type = 'disposition.override'
        ORDER BY ingest_seq
        """
    ).fetchall()
    for row in overrides:
        lead_id = find_sibling_lead(conn, row["workflow_id"], row["call_id"], row["event_id"])
        if lead_id is not None:
            _attach(conn, catalogue, row, lead_id)


def _attach(
    conn: sqlite3.Connection,
    catalogue: dict[tuple[str, str], Disposition],
    row: sqlite3.Row,
    lead_id: int,
) -> None:
    ignored = 0
    if not is_known_disposition(
        catalogue, row["workflow_id"], row["event_type"], row["payload_json"]
    ):
        ignored = 1
    conn.execute(
        """
        UPDATE events
        SET lead_id = ?, ignored = ?
        WHERE event_id = ? AND lead_id IS NULL
        """,
        (lead_id, ignored, row["event_id"]),
    )


def note_duplicate(conn: sqlite3.Connection) -> None:
    add_quality(conn, duplicate_events_dropped=1)


def note_malformed(conn: sqlite3.Connection) -> None:
    add_quality(conn, malformed_events=1)


def note_event_line(conn: sqlite3.Connection) -> None:
    add_quality(conn, events_total=1)


def event_exists(conn: sqlite3.Connection, event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return row is not None
