"""Validate one lead row and merge it into the lead table."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from solution.db import add_quality, allocate_seq
from solution.normalize import canonical_phone, parse_date, parse_decimal, parse_enum
from solution.pii import encrypt_name, phone_hmac
from solution.reference import FieldSpec, Schema


@dataclass(frozen=True)
class AcceptedRow:
    values: dict[str, str | None]
    warnings: int


@dataclass(frozen=True)
class RejectedRow:
    reason: str


def map_row(schema: Schema, csv_row: dict) -> dict[str, str]:
    """Map a CSV dict onto schema field names using normalised headers.

    The first matching header wins. Columns that match nothing are ignored.
    """
    by_header: dict[str, str] = {}
    for header, raw in csv_row.items():
        if header is None:
            continue
        key = _norm_key(header)
        if key not in by_header:
            by_header[key] = "" if raw is None else str(raw)
    mapped: dict[str, str] = {}
    for spec in schema.fields:
        raw = ""
        for candidate in _field_keys(spec):
            if candidate in by_header:
                raw = by_header[candidate]
                break
        mapped[spec.name] = raw
    return mapped


def _norm_key(header: str) -> str:
    from solution.normalize import norm_header

    return norm_header(header)


def _field_keys(spec: FieldSpec) -> list[str]:
    from solution.normalize import norm_header

    return [norm_header(spec.name), *[norm_header(alias) for alias in spec.aliases]]


def is_blank_row(csv_row: dict) -> bool:
    for key, raw in csv_row.items():
        if key is None:
            continue
        if raw is not None and str(raw).strip() != "":
            return False
    return True


def evaluate_row(schema: Schema, raw_fields: dict[str, str]) -> AcceptedRow | RejectedRow:
    """Walk fields in schema order. The first hard failure is the only reason.

    A present but invalid phone rejects the row. An invalid optional field
    becomes null and counts as one warning, and only when the row is kept.
    """
    values: dict[str, str | None] = {}
    warnings = 0
    for spec in schema.fields:
        parsed, status = _parse_field(spec, raw_fields.get(spec.name, ""))
        if status == "empty":
            if spec.required:
                return RejectedRow(f"missing_required:{spec.name}")
            values[spec.name] = None
            continue
        if status == "invalid":
            if spec.type == "phone":
                return RejectedRow("invalid_phone")
            if spec.required:
                return RejectedRow(f"missing_required:{spec.name}")
            values[spec.name] = None
            warnings += 1
            continue
        values[spec.name] = parsed
    return AcceptedRow(values=values, warnings=warnings)


def _parse_field(spec: FieldSpec, raw: str) -> tuple[str | None, str]:
    text = raw.strip() if raw else ""
    if text == "":
        return None, "empty"
    if spec.type == "string":
        return text, "ok"
    if spec.type == "phone":
        canonical = canonical_phone(text)
        if canonical is None:
            return None, "invalid"
        return canonical, "ok"
    if spec.type == "date":
        parsed = parse_date(text)
        if parsed is None:
            return None, "invalid"
        return parsed, "ok"
    if spec.type == "decimal":
        parsed = parse_decimal(text)
        if parsed is None:
            return None, "invalid"
        return parsed, "ok"
    if spec.type == "enum":
        parsed = parse_enum(text, spec.values)
        if parsed is None:
            return None, "invalid"
        return parsed, "ok"
    return None, "invalid"


def apply_defaults(schema: Schema, values: dict[str, str | None]) -> None:
    """Fill defaults on fields that are still null. Call this after merging."""
    for spec in schema.fields:
        if values.get(spec.name) is None and spec.default is not None:
            values[spec.name] = spec.default


def store_lead(
    conn: sqlite3.Connection,
    schema: Schema,
    batch_id: str,
    accepted: AcceptedRow,
) -> None:
    """Insert a new lead or merge a duplicate. ``batch_id`` is permanent."""
    phone = accepted.values.get("phone")
    if not phone:
        raise RuntimeError("A stored lead has no canonical phone")
    digest = phone_hmac(phone)
    existing = conn.execute(
        "SELECT * FROM leads WHERE workflow_id = ? AND phone_hmac = ?",
        (schema.workflow_id, digest),
    ).fetchone()
    if existing is None:
        _insert_lead(conn, schema, batch_id, accepted, digest, phone)
        return
    _merge_lead(conn, schema, accepted, existing)


def _non_pii(schema: Schema, values: dict[str, str | None]) -> dict[str, str | None]:
    return {spec.name: values.get(spec.name) for spec in schema.fields if not spec.pii}


def _insert_lead(
    conn: sqlite3.Connection,
    schema: Schema,
    batch_id: str,
    accepted: AcceptedRow,
    digest: str,
    phone: str,
) -> None:
    values = dict(accepted.values)
    apply_defaults(schema, values)
    name = values.get("customer_name")
    ref_name = schema.reference_field
    conn.execute(
        """
        INSERT INTO leads (
            workflow_id, phone_hmac, phone_last4, batch_id, external_ref,
            fields_json, name_ciphertext, ingest_seq
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            schema.workflow_id,
            digest,
            phone[-4:],
            batch_id,
            values.get(ref_name) if ref_name else None,
            json.dumps(_non_pii(schema, values), ensure_ascii=False, sort_keys=True),
            encrypt_name(name) if name else None,
            allocate_seq(conn),
        ),
    )


def _merge_lead(
    conn: sqlite3.Connection,
    schema: Schema,
    accepted: AcceptedRow,
    existing: sqlite3.Row,
) -> None:
    merged: dict[str, str | None] = json.loads(existing["fields_json"])
    for spec in schema.fields:
        if spec.pii:
            continue
        incoming = accepted.values.get(spec.name)
        if incoming is not None:
            merged[spec.name] = incoming
    full = {spec.name: merged.get(spec.name) for spec in schema.fields if not spec.pii}
    apply_defaults(schema, full)
    for spec in schema.fields:
        if not spec.pii:
            merged[spec.name] = full.get(spec.name)
    name_ciphertext = existing["name_ciphertext"]
    incoming_name = accepted.values.get("customer_name")
    if incoming_name:
        name_ciphertext = encrypt_name(incoming_name)
    ref_name = schema.reference_field
    conn.execute(
        """
        UPDATE leads
        SET external_ref = ?, fields_json = ?, name_ciphertext = ?
        WHERE id = ?
        """,
        (
            merged.get(ref_name) if ref_name else existing["external_ref"],
            json.dumps(merged, ensure_ascii=False, sort_keys=True),
            name_ciphertext,
            existing["id"],
        ),
    )
    add_quality(conn, duplicate_lead_rows_merged=1)
