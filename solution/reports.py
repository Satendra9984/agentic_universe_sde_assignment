"""Derive calls, buckets, and the five report files from the ledger."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from solution.db import init_db
from solution.events import payload_code
from solution.normalize import format_ist_iso, ist_date
from solution.reference import Disposition

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class DispositionEvent:
    event_type: str
    occurred_at: str
    ingest_seq: int
    code: str


@dataclass(frozen=True)
class CallFacts:
    lead_id: int
    workflow_id: str
    call_id: str
    initiated_at: datetime
    connected: bool
    telephony_failed: bool
    effective: str | None


def choose_effective(events: list[DispositionEvent]) -> str | None:
    """Latest override wins outright. A later ``disposition.set`` does not beat it.

    If there is no override, the latest ``disposition.set`` is used. Equal
    timestamps break toward the later ingested event.
    """
    overrides = [event for event in events if event.event_type == "disposition.override" and event.code]
    chosen = _latest(overrides)
    if chosen is not None:
        return chosen.code
    sets = [event for event in events if event.event_type == "disposition.set" and event.code]
    chosen = _latest(sets)
    return chosen.code if chosen is not None else None


def _latest(events: list[DispositionEvent]) -> DispositionEvent | None:
    if not events:
        return None
    return max(events, key=lambda event: (event.occurred_at, event.ingest_seq))


def bucket_for(calls: list[CallFacts], is_terminal) -> str:
    """First matching bucket. ``is_terminal`` receives an effective disposition code."""
    if not calls:
        return "yet_to_reach_out"
    if any(call.effective and is_terminal(call.effective) for call in calls):
        return "completed"
    if all(call.telephony_failed for call in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def current_disposition(calls: list[CallFacts]) -> str:
    labelled = [call for call in calls if call.effective]
    if not labelled:
        return ""
    latest = max(labelled, key=lambda call: (call.initiated_at, call.call_id))
    return latest.effective or ""


def ever_success(calls: list[CallFacts], is_success) -> bool:
    return any(call.effective and is_success(call.effective) for call in calls)


def last_call_at(calls: list[CallFacts]) -> str:
    if not calls:
        return ""
    latest = max(calls, key=lambda call: (call.initiated_at, call.call_id))
    return format_ist_iso(latest.initiated_at)


def load_calls(
    conn: sqlite3.Connection,
    catalogue: dict[tuple[str, str], Disposition],
) -> list[CallFacts]:
    """A call exists only when a linked, non-ignored ``call.initiated`` exists.

    Ignored disposition rows stay out, so an unknown code cannot become the
    effective outcome. Orphan rows are already excluded by ``lead_id``.
    """
    rows = conn.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id, occurred_at_utc,
               payload_json, lead_id, ingest_seq
        FROM events
        WHERE lead_id IS NOT NULL AND ignored = 0 AND call_id IS NOT NULL
        ORDER BY ingest_seq
        """
    ).fetchall()
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["workflow_id"], row["call_id"])].append(row)

    calls: list[CallFacts] = []
    for (workflow_id, call_id), events in grouped.items():
        initiated = [event for event in events if event["event_type"] == "call.initiated"]
        if not initiated:
            continue
        initiated.sort(key=lambda event: (event["occurred_at_utc"] or "", event["ingest_seq"]))
        first = initiated[0]
        when = _parse_utc(first["occurred_at_utc"])
        disposition_events = []
        for event in events:
            if not str(event["event_type"]).startswith("disposition."):
                continue
            code = payload_code(event["payload_json"]) or ""
            if (workflow_id, code) not in catalogue:
                continue
            disposition_events.append(
                DispositionEvent(
                    event_type=event["event_type"],
                    occurred_at=event["occurred_at_utc"] or "",
                    ingest_seq=int(event["ingest_seq"]),
                    code=code,
                )
            )
        calls.append(
            CallFacts(
                lead_id=int(first["lead_id"]),
                workflow_id=workflow_id,
                call_id=call_id,
                initiated_at=when,
                connected=any(event["event_type"] == "call.connected" for event in events),
                telephony_failed=any(
                    event["event_type"] == "call.telephony_failed" for event in events
                ),
                effective=choose_effective(disposition_events),
            )
        )
    return calls


def _parse_utc(text: str | None) -> datetime:
    if not text:
        return _EPOCH
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def write_reports(
    conn: sqlite3.Connection,
    out_dir: Path,
    catalogue: dict[tuple[str, str], Disposition],
) -> None:
    init_db(conn)
    out_dir.mkdir(parents=True, exist_ok=True)
    calls = load_calls(conn, catalogue)
    by_lead: dict[int, list[CallFacts]] = defaultdict(list)
    for call in calls:
        by_lead[call.lead_id].append(call)

    def is_terminal(workflow_id: str, code: str) -> bool:
        item = catalogue.get((workflow_id, code))
        return bool(item and item.is_terminal)

    def is_success(workflow_id: str, code: str) -> bool:
        item = catalogue.get((workflow_id, code))
        return bool(item and item.is_success)

    leads = conn.execute(
        """
        SELECT id, workflow_id, phone_last4, batch_id, external_ref
        FROM leads
        """
    ).fetchall()
    lead_rows = []
    batch_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "total_leads": 0,
            "yet_to_reach_out": 0,
            "in_progress_telephony_failed": 0,
            "in_progress_dialed": 0,
            "completed": 0,
        }
    )
    for lead in leads:
        lead_calls = by_lead.get(int(lead["id"]), [])
        workflow_id = lead["workflow_id"]
        bucket = bucket_for(
            lead_calls,
            lambda code, workflow_id=workflow_id: is_terminal(workflow_id, code),
        )
        key = (workflow_id, lead["batch_id"])
        batch_counts[key]["total_leads"] += 1
        batch_counts[key][bucket] += 1
        lead_rows.append(
            {
                "workflow_id": workflow_id,
                "external_ref": lead["external_ref"] or "",
                "batch_id": lead["batch_id"],
                "phone_masked": f"+91******{lead['phone_last4']}",
                "bucket": bucket,
                "total_calls": len(lead_calls),
                "connected_calls": sum(1 for call in lead_calls if call.connected),
                "last_call_at": last_call_at(lead_calls),
                "current_disposition": current_disposition(lead_calls),
                "ever_success": "true" if ever_success(
                    lead_calls,
                    lambda code, workflow_id=workflow_id: is_success(workflow_id, code),
                ) else "false",
            }
        )
    lead_rows.sort(key=lambda row: (row["workflow_id"], row["external_ref"]))

    _write_csv(
        out_dir / "batch_summary.csv",
        [
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ],
        [
            [
                workflow_id,
                batch_id,
                counts["total_leads"],
                counts["yet_to_reach_out"],
                counts["in_progress_telephony_failed"],
                counts["in_progress_dialed"],
                counts["completed"],
            ]
            for (workflow_id, batch_id), counts in sorted(batch_counts.items())
        ],
    )
    _write_csv(
        out_dir / "daily_overview.csv",
        [
            "workflow_id",
            "date",
            "total_calls",
            "connected_calls",
            "connectivity_rate",
            "success_leads",
        ],
        _daily_rows(calls, is_success),
    )
    _write_csv(
        out_dir / "lead_report.csv",
        [
            "workflow_id",
            "external_ref",
            "batch_id",
            "phone_masked",
            "bucket",
            "total_calls",
            "connected_calls",
            "last_call_at",
            "current_disposition",
            "ever_success",
        ],
        [
            [
                row["workflow_id"],
                row["external_ref"],
                row["batch_id"],
                row["phone_masked"],
                row["bucket"],
                row["total_calls"],
                row["connected_calls"],
                row["last_call_at"],
                row["current_disposition"],
                row["ever_success"],
            ]
            for row in lead_rows
        ],
    )

    rejections = conn.execute(
        "SELECT workflow_id, batch_id, row_number, reason FROM rejections"
    ).fetchall()
    rejection_rows = sorted(
        rejections,
        key=lambda row: (row["workflow_id"], row["batch_id"], int(row["row_number"])),
    )
    _write_csv(
        out_dir / "rejections.csv",
        ["workflow_id", "batch_id", "row_number", "reason"],
        [
            [row["workflow_id"], row["batch_id"], int(row["row_number"]), row["reason"]]
            for row in rejection_rows
        ],
    )
    (out_dir / "data_quality.json").write_text(
        json.dumps(_quality_document(conn), indent=2) + "\n",
        encoding="utf-8",
    )


def _daily_rows(calls: list[CallFacts], is_success) -> list[list]:
    grouped: dict[tuple[str, str], dict] = {}
    for call in calls:
        key = (call.workflow_id, ist_date(call.initiated_at))
        bucket = grouped.setdefault(
            key,
            {"total": 0, "connected": 0, "success_leads": set()},
        )
        bucket["total"] += 1
        if call.connected:
            bucket["connected"] += 1
        if call.effective and is_success(call.workflow_id, call.effective):
            bucket["success_leads"].add(call.lead_id)
    rows = []
    for (workflow_id, day), bucket in sorted(grouped.items()):
        rate = Decimal(bucket["connected"]) / Decimal(bucket["total"])
        rows.append(
            [
                workflow_id,
                day,
                bucket["total"],
                bucket["connected"],
                f"{rate:.4f}",
                len(bucket["success_leads"]),
            ]
        )
    return rows


def _quality_document(conn: sqlite3.Connection) -> dict:
    quality = conn.execute("SELECT * FROM quality WHERE id = 1").fetchone()
    reasons = conn.execute(
        "SELECT reason, COUNT(*) AS n FROM rejections GROUP BY reason ORDER BY reason"
    ).fetchall()
    orphans = conn.execute("SELECT COUNT(*) AS n FROM events WHERE lead_id IS NULL").fetchone()
    unknown = conn.execute("SELECT COUNT(*) AS n FROM events WHERE ignored = 1").fetchone()
    return {
        "lead_rows_total": int(quality["lead_rows_total"]) if quality else 0,
        "lead_rows_rejected": int(quality["lead_rows_rejected"]) if quality else 0,
        "rejections_by_reason": {row["reason"]: int(row["n"]) for row in reasons},
        "duplicate_lead_rows_merged": int(quality["duplicate_lead_rows_merged"]) if quality else 0,
        "field_warnings": int(quality["field_warnings"]) if quality else 0,
        "events_total": int(quality["events_total"]) if quality else 0,
        "malformed_events": int(quality["malformed_events"]) if quality else 0,
        "duplicate_events_dropped": int(quality["duplicate_events_dropped"]) if quality else 0,
        "orphan_events": int(orphans["n"]),
        "unknown_disposition_events": int(unknown["n"]),
    }


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
