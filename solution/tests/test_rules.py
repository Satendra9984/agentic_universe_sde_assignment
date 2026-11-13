"""Row rejection, disposition choice, and lead buckets."""

from datetime import datetime, timezone

from solution.leads import evaluate_row, map_row
from solution.reference import load_schemas
from solution.reports import (
    CallFacts,
    DispositionEvent,
    bucket_for,
    choose_effective,
)


def _motor():
    return load_schemas()["motor_renewal"]


def _row(**overrides):
    base = {
        "customer_name": "Aarav Menon",
        "phone": "9932302346",
        "policy_number": "MOT/1",
        "policy_expiry": "2026-11-21",
        "premium_amount": "100",
        "preferred_language": "en",
    }
    base.update(overrides)
    return base


def test_missing_name_wins_over_a_later_bad_phone():
    outcome = evaluate_row(_motor(), _row(customer_name="  ", phone="N/A"))
    assert outcome.reason == "missing_required:customer_name"


def test_invalid_phone_is_its_own_reason_when_the_name_is_present():
    outcome = evaluate_row(_motor(), _row(phone="+91 55555 55555"))
    assert outcome.reason == "invalid_phone"


def test_empty_phone_is_missing_not_invalid():
    outcome = evaluate_row(_motor(), _row(phone=""))
    assert outcome.reason == "missing_required:phone"


def test_invalid_optional_fields_warn_and_do_not_reject():
    outcome = evaluate_row(
        _motor(),
        _row(policy_expiry="31/02/2026", preferred_language="Hindi", premium_amount="N/A"),
    )
    assert outcome.warnings == 3
    assert outcome.values["policy_expiry"] is None
    assert outcome.values["preferred_language"] is None
    assert outcome.values["premium_amount"] is None
    assert outcome.values["phone"] == "+919932302346"


def test_alias_headers_map_onto_schema_fields():
    schema = _motor()
    mapped = map_row(
        schema,
        {
            "Insured Name": "Late Lead",
            "Mobile No": "9000000001",
            "Policy No": "MOT/LATE",
            "Language": "EN",
            "Premium": "INR 18678.3",
            "Expiry Date": "05/11/2026",
            "agent_notes": "called before",
        },
    )
    outcome = evaluate_row(schema, mapped)
    assert outcome.values["customer_name"] == "Late Lead"
    assert outcome.values["phone"] == "+919000000001"
    assert outcome.values["policy_number"] == "MOT/LATE"
    assert outcome.values["preferred_language"] == "en"
    assert outcome.values["premium_amount"] == "18678.30"
    assert outcome.values["policy_expiry"] == "2026-11-05"


def test_an_override_beats_a_later_agent_disposition():
    events = [
        DispositionEvent("disposition.override", "2026-10-01T00:00:00Z", 1, "not_interested"),
        DispositionEvent("disposition.set", "2026-10-02T00:00:00Z", 2, "renewed"),
    ]
    assert choose_effective(events) == "not_interested"


def test_latest_override_wins_and_timestamp_ties_use_ingest_order():
    overrides = [
        DispositionEvent("disposition.override", "2026-10-01T00:00:00Z", 1, "not_interested"),
        DispositionEvent("disposition.override", "2026-10-03T00:00:00Z", 2, "renewed"),
    ]
    assert choose_effective(overrides) == "renewed"
    tied = [
        DispositionEvent("disposition.set", "2026-10-01T00:00:00Z", 1, "renewed"),
        DispositionEvent("disposition.set", "2026-10-01T00:00:00Z", 5, "not_interested"),
    ]
    assert choose_effective(tied) == "not_interested"
    assert choose_effective([]) is None


def _call(effective, *, failed=False, connected=False, lead=1, call_id="c1"):
    return CallFacts(
        lead_id=lead,
        workflow_id="motor_renewal",
        call_id=call_id,
        initiated_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        connected=connected,
        telephony_failed=failed,
        effective=effective,
    )


def test_bucket_priority():
    terminal = lambda code: code in {"renewed", "dnd", "not_interested"}
    assert bucket_for([], terminal) == "yet_to_reach_out"
    assert bucket_for([_call("dnd", failed=True)], terminal) == "completed"
    assert bucket_for([_call(None, failed=True), _call(None, failed=True, call_id="c2")], terminal) == (
        "in_progress_telephony_failed"
    )
    assert bucket_for([_call(None, failed=True), _call(None, call_id="c2")], terminal) == (
        "in_progress_dialed"
    )
    assert bucket_for([_call("payment_link_sent", connected=True)], terminal) == "in_progress_dialed"
