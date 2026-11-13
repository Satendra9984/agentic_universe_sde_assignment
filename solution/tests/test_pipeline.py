"""End-to-end ingest: messy drops, late leads, idempotency, and the five files."""

import json
import sqlite3
from pathlib import Path

from solution.cli import main
from solution.pii import decrypt_name

MOTOR_HEADER = [
    "customer_name",
    "phone",
    "policy_number",
    "policy_expiry",
    "premium_amount",
    "preferred_language",
]


def _write_csv(path: Path, header: list[str], rows: list[list[str] | None]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            if row is None:
                writer.writerow([""] * len(header))
            else:
                writer.writerow(row)


def _event(event_id, event_type, workflow, call_id, occurred, phone=None, payload=None) -> str:
    body = {
        "event_id": event_id,
        "event_type": event_type,
        "workflow_id": workflow,
        "call_id": call_id,
        "occurred_at": occurred,
        "payload": payload or {},
    }
    if phone is not None:
        body["phone"] = phone
    return json.dumps(body)


def _build_drops(root: Path) -> list[str]:
    motor = root / "drop_1" / "leads" / "motor_renewal" / "batch_001.csv"
    _write_csv(
        motor,
        MOTOR_HEADER,
        [
            ["Aarav Menon", "+916463745499", "MOT/1", "21-11-2026", "₹27,554", "mr"],
            None,
            ["", "9932302346", "MOT/2", "27/10/2026", "6850", "hi"],
            ["Bad Phone", "+91 55555 55555", "MOT/3", "2026-11-23", "100", "en"],
            ["No Date", "8880395512", "MOT/4", "31/02/2026", "10", "Hindi"],
            ["Aarav Menon", "6463745499", "MOT/1B", "01-Dec-2026", "", "hi"],
            ["Fail User", "7718750036", "MOT/9", "2026-10-01", "100", "en"],
            ["Quiet User", "9123456789", "MOT/8", "", "", ""],
        ],
    )
    feedback = root / "drop_1" / "leads" / "policy_feedback" / "batch_001.csv"
    _write_csv(
        feedback,
        ["customer_name", "phone", "order_id", "product", "order_date", "preferred_language"],
        [["Saanvi", "7864865007", "ORD-1", "Laptop", "2026-09-11", "HI"]],
    )
    aarav_init = _event(
        "e-aarav-init",
        "call.initiated",
        "motor_renewal",
        "call_a",
        "2026-10-03 22:30:00",
        "+91 64637 45499",
    )
    events = [
        _event(
            "e-aarav-override",
            "disposition.override",
            "motor_renewal",
            "call_a",
            "2026-10-04T10:00:00+05:30",
            payload={"disposition_code": "not_interested", "overridden_by": "s", "reason": "qa"},
        ),
        aarav_init,
        _event(
            "e-aarav-conn",
            "call.connected",
            "motor_renewal",
            "call_a",
            "2026-10-03 22:30:10",
            "+916463745499",
        ),
        _event(
            "e-aarav-end",
            "call.ended",
            "motor_renewal",
            "call_a",
            "2026-10-03 22:32:00",
            "+916463745499",
            {"duration_sec": 90, "end_reason": "completed"},
        ),
        _event(
            "e-aarav-set",
            "disposition.set",
            "motor_renewal",
            "call_a",
            "2026-10-03 22:33:00",
            "+916463745499",
            {"disposition_code": "renewed"},
        ),
        _event(
            "e-fail-init",
            "call.initiated",
            "motor_renewal",
            "call_f",
            "2026-10-02 10:00:00",
            "07718750036",
        ),
        _event(
            "e-fail-tel",
            "call.telephony_failed",
            "motor_renewal",
            "call_f",
            "2026-10-02 10:00:05",
            "7718750036",
            {"reason": "sip_503"},
        ),
        _event(
            "e-saanvi-init",
            "call.initiated",
            "policy_feedback",
            "call_s",
            "2026-10-03T20:00:00Z",
            "91-78648-65007",
        ),
        _event(
            "e-saanvi-bad",
            "disposition.set",
            "policy_feedback",
            "call_s",
            "2026-10-03T20:01:00Z",
            "7864865007",
            {"disposition_code": "RENEWD"},
        ),
        _event(
            "e-orphan",
            "call.initiated",
            "motor_renewal",
            "call_o",
            "2026-10-01 10:00:00",
            "+919999999999",
        ),
        _event(
            "e-late-init",
            "call.initiated",
            "motor_renewal",
            "call_l",
            "2026-10-05 11:00:00",
            "9000000001",
        ),
        _event(
            "e-late-conn",
            "call.connected",
            "motor_renewal",
            "call_l",
            "2026-10-05 11:00:08",
            "9000000001",
        ),
        _event(
            "e-late-set",
            "disposition.set",
            "motor_renewal",
            "call_l",
            "2026-10-05 11:05:00",
            "9000000001",
            {"disposition_code": "renewed"},
        ),
        aarav_init,
        "{bad",
    ]
    event_path = root / "drop_1" / "events" / "events_0001.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with_blank = events[:4] + [""] + events[4:]
    event_path.write_text("\n".join(with_blank) + "\n", encoding="utf-8")

    late = root / "drop_2" / "leads" / "motor_renewal" / "batch_003.csv"
    _write_csv(
        late,
        ["Language", "Insured Name", "Policy No", "Mobile No", "Expiry Date", "Premium", "agent_notes"],
        [["en", "Late Lead", "MOT/LATE", "9000000001", "05/11/2026", "INR 18678.3", "note"]],
    )
    return events


def _run(db: Path, drop: Path | None = None, out: Path | None = None) -> int:
    args = ["--db", str(db)]
    if drop is not None:
        args.extend(["ingest", str(drop)])
    else:
        args.extend(["report", "--out", str(out)])
    return main(args)


def test_pipeline_matches_the_report_contract_and_is_idempotent(tmp_path: Path):
    root = tmp_path / "drops"
    events = _build_drops(root)
    db = tmp_path / "ledger.sqlite"
    out1 = tmp_path / "out1"
    assert _run(db, drop=root / "drop_1") == 0
    assert _run(db, out=out1) == 0
    after_day_1 = json.loads((out1 / "data_quality.json").read_text(encoding="utf-8"))
    assert after_day_1["orphan_events"] == 4
    assert after_day_1["unknown_disposition_events"] == 1
    assert after_day_1["lead_rows_total"] == 8
    assert "MOT/LATE" not in (out1 / "lead_report.csv").read_text(encoding="utf-8")
    assert "2026-10-05" not in (out1 / "daily_overview.csv").read_text(encoding="utf-8")

    out = tmp_path / "reports"
    assert _run(db, drop=root / "drop_2") == 0
    assert _run(db, out=out) == 0

    assert (out / "batch_summary.csv").read_text(encoding="utf-8") == (
        "workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,"
        "in_progress_dialed,completed\n"
        "motor_renewal,batch_001,4,2,1,0,1\n"
        "motor_renewal,batch_003,1,0,0,0,1\n"
        "policy_feedback,batch_001,1,0,0,1,0\n"
    )
    assert (out / "daily_overview.csv").read_text(encoding="utf-8") == (
        "workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads\n"
        "motor_renewal,2026-10-02,1,0,0.0000,0\n"
        "motor_renewal,2026-10-03,1,1,1.0000,0\n"
        "motor_renewal,2026-10-05,1,1,1.0000,1\n"
        "policy_feedback,2026-10-04,1,0,0.0000,0\n"
    )
    assert (out / "lead_report.csv").read_text(encoding="utf-8") == (
        "workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,"
        "last_call_at,current_disposition,ever_success\n"
        "motor_renewal,MOT/1B,batch_001,+91******5499,completed,1,1,"
        "2026-10-03T22:30:00+05:30,not_interested,false\n"
        "motor_renewal,MOT/4,batch_001,+91******5512,yet_to_reach_out,0,0,,,false\n"
        "motor_renewal,MOT/8,batch_001,+91******6789,yet_to_reach_out,0,0,,,false\n"
        "motor_renewal,MOT/9,batch_001,+91******0036,in_progress_telephony_failed,1,0,"
        "2026-10-02T10:00:00+05:30,,false\n"
        "motor_renewal,MOT/LATE,batch_003,+91******0001,completed,1,1,"
        "2026-10-05T11:00:00+05:30,renewed,true\n"
        "policy_feedback,ORD-1,batch_001,+91******5007,in_progress_dialed,1,0,"
        "2026-10-04T01:30:00+05:30,,false\n"
    )
    assert (out / "rejections.csv").read_text(encoding="utf-8") == (
        "workflow_id,batch_id,row_number,reason\n"
        "motor_renewal,batch_001,2,missing_required:customer_name\n"
        "motor_renewal,batch_001,3,invalid_phone\n"
    )
    quality = {
        "lead_rows_total": 9,
        "lead_rows_rejected": 2,
        "rejections_by_reason": {
            "invalid_phone": 1,
            "missing_required:customer_name": 1,
        },
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 2,
        "events_total": len(events),
        "malformed_events": 1,
        "duplicate_events_dropped": 1,
        "orphan_events": 1,
        "unknown_disposition_events": 1,
    }
    assert (out / "data_quality.json").read_text(encoding="utf-8") == json.dumps(quality, indent=2) + "\n"
    assert "5555555555" not in (out / "rejections.csv").read_text(encoding="utf-8")

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT external_ref, batch_id, fields_json, name_ciphertext FROM leads WHERE external_ref = ?",
            ("MOT/1B",),
        ).fetchone()
        fields = json.loads(row[2])
        assert row[0] == "MOT/1B"
        assert row[1] == "batch_001"
        assert fields["premium_amount"] == "27554.00"
        assert fields["preferred_language"] == "hi"
        assert fields["policy_expiry"] == "2026-12-01"
        assert decrypt_name(row[3]) == "Aarav Menon"
        late = conn.execute(
            "SELECT fields_json, batch_id FROM leads WHERE external_ref = ?",
            ("MOT/LATE",),
        ).fetchone()
        late_fields = json.loads(late[0])
        assert late[1] == "batch_003"
        assert late_fields["premium_amount"] == "18678.30"
        assert late_fields["preferred_language"] == "en"
        no_date = json.loads(
            conn.execute(
                "SELECT fields_json FROM leads WHERE external_ref = ?",
                ("MOT/4",),
            ).fetchone()[0]
        )
        assert no_date["policy_expiry"] is None
        assert no_date["preferred_language"] == "en"
    finally:
        conn.close()

    raw_db = db.read_bytes()
    assert b"Aarav Menon" not in raw_db
    assert b"+916463745499" not in raw_db
    assert b"9000000001" not in raw_db

    snapshot = {path.name: path.read_bytes() for path in out.iterdir()}
    assert _run(db, drop=root / "drop_1") == 0
    assert _run(db, drop=root / "drop_2") == 0
    assert _run(db, out=out) == 0
    assert {path.name: path.read_bytes() for path in out.iterdir()} == snapshot


def test_unknown_code_stays_an_orphan_until_the_lead_exists(tmp_path: Path):
    root = tmp_path / "drops"
    _write_csv(
        root / "drop_2" / "leads" / "motor_renewal" / "batch_003.csv",
        MOTOR_HEADER,
        [["Late", "9000000002", "MOT/U", "", "", "en"]],
    )
    event_path = root / "drop_1" / "events" / "events_0001.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _event("u-init", "call.initiated", "motor_renewal", "call_u", "2026-10-06 09:00:00", "9000000002"),
        _event(
            "u-set",
            "disposition.set",
            "motor_renewal",
            "call_u",
            "2026-10-06 09:05:00",
            "9000000002",
            {"disposition_code": "NOPE"},
        ),
    ]
    event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "ledger.sqlite"
    early = tmp_path / "early"
    assert _run(db, drop=root / "drop_1") == 0
    assert _run(db, out=early) == 0
    early_quality = json.loads((early / "data_quality.json").read_text(encoding="utf-8"))
    assert early_quality["orphan_events"] == 2
    assert early_quality["unknown_disposition_events"] == 0

    final = tmp_path / "final"
    assert _run(db, drop=root / "drop_2") == 0
    assert _run(db, out=final) == 0
    quality = json.loads((final / "data_quality.json").read_text(encoding="utf-8"))
    assert quality["orphan_events"] == 0
    assert quality["unknown_disposition_events"] == 1
    report = (final / "lead_report.csv").read_text(encoding="utf-8")
    assert "MOT/U" in report
    assert "in_progress_dialed" in report
    assert "NOPE" not in report


def test_unknown_override_does_not_replace_a_valid_set(tmp_path: Path):
    root = tmp_path / "drop"
    _write_csv(
        root / "leads" / "motor_renewal" / "batch_001.csv",
        MOTOR_HEADER,
        [["Won", "9000000003", "MOT/W", "", "", "en"]],
    )
    lines = [
        _event("w-init", "call.initiated", "motor_renewal", "call_w", "2026-10-06 09:00:00", "9000000003"),
        _event(
            "w-set",
            "disposition.set",
            "motor_renewal",
            "call_w",
            "2026-10-06 09:10:00",
            "9000000003",
            {"disposition_code": "renewed"},
        ),
        _event(
            "w-over",
            "disposition.override",
            "motor_renewal",
            "call_w",
            "2026-10-06 12:00:00",
            payload={"disposition_code": "NOPE", "overridden_by": "s", "reason": "qa"},
        ),
    ]
    event_path = root / "events" / "events_0001.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "ledger.sqlite"
    out = tmp_path / "out"
    assert _run(db, drop=root) == 0
    assert _run(db, out=out) == 0
    text = (out / "lead_report.csv").read_text(encoding="utf-8")
    assert "MOT/W" in text
    assert "renewed" in text
    assert ",true" in text
    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
    assert quality["unknown_disposition_events"] == 1


def test_same_phone_in_two_workflows_is_two_leads(tmp_path: Path):
    root = tmp_path / "drop"
    _write_csv(
        root / "leads" / "motor_renewal" / "batch_001.csv",
        MOTOR_HEADER,
        [["A", "9000000004", "MOT/A", "", "", "en"]],
    )
    _write_csv(
        root / "leads" / "policy_feedback" / "batch_001.csv",
        ["customer_name", "phone", "order_id", "product", "order_date", "preferred_language"],
        [["A", "9000000004", "ORD-A", "", "", "en"]],
    )
    db = tmp_path / "ledger.sqlite"
    out = tmp_path / "out"
    assert _run(db, drop=root) == 0
    assert _run(db, out=out) == 0
    text = (out / "lead_report.csv").read_text(encoding="utf-8")
    assert "motor_renewal,MOT/A," in text
    assert "policy_feedback,ORD-A," in text


def test_missing_drop_returns_an_error(tmp_path: Path):
    assert main(["--db", str(tmp_path / "x.sqlite"), "ingest", str(tmp_path / "nope")]) == 1
