"""Phone, date, money, header, and timestamp rules."""

from datetime import timezone

from solution.normalize import (
    canonical_phone,
    format_ist_iso,
    ist_date,
    norm_header,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_occurred_at,
)


def test_phone_accepts_the_formats_in_the_sample():
    assert canonical_phone("9932302346") == "+919932302346"
    assert canonical_phone("06832582386") == "+916832582386"
    assert canonical_phone("+91 79328 96340") == "+917932896340"
    assert canonical_phone("(+91) 8196106462") == "+918196106462"
    assert canonical_phone("91-81518-64492") == "+918151864492"
    assert canonical_phone("+916463745499") == "+916463745499"
    assert canonical_phone("09123456789") == "+919123456789"


def test_phone_rejects_too_short_bad_prefix_landline_and_foreign():
    assert canonical_phone("123456789") is None
    assert canonical_phone("5123456789") is None
    assert canonical_phone("022-2345-6789") is None
    assert canonical_phone("N/A") is None
    assert canonical_phone("+1 415 555 0134") is None
    assert canonical_phone("123456789012") is None


def test_dates_are_day_first_and_impossible_days_fail():
    assert parse_date("2026-11-23") == "2026-11-23"
    assert parse_date("27/10/2026") == "2026-10-27"
    assert parse_date("21-11-2026") == "2026-11-21"
    assert parse_date("01-Dec-2026") == "2026-12-01"
    assert parse_date("01-dec-2026") == "2026-12-01"
    assert parse_date("2024-02-29") == "2024-02-29"
    assert parse_date("31/02/2026") is None
    assert parse_date("2026-13-01") is None
    assert parse_date("29-Feb-2026") is None
    assert parse_date("soon") is None
    assert parse_date("TBD") is None


def test_decimals_strip_currency_and_round_half_up():
    assert parse_decimal("₹27,554") == "27554.00"
    assert parse_decimal("Rs. 31629") == "31629.00"
    assert parse_decimal("Rs.31629") == "31629.00"
    assert parse_decimal("INR 25301.4") == "25301.40"
    assert parse_decimal("6850") == "6850.00"
    assert parse_decimal("1,00,000") == "100000.00"
    assert parse_decimal("1.005") == "1.01"
    assert parse_decimal("1.004") == "1.00"
    assert parse_decimal("0") == "0.00"
    assert parse_decimal("-1") is None
    assert parse_decimal("N/A") is None
    assert parse_decimal("twelve thousand") is None


def test_enum_is_case_insensitive_and_stores_lower_case():
    values = ["en", "hi", "ta", "kn", "te", "mr"]
    assert parse_enum("HI", values) == "hi"
    assert parse_enum(" en ", values) == "en"
    assert parse_enum("Hindi", values) is None
    assert parse_enum("english", values) is None


def test_header_normalisation_matches_aliases():
    assert norm_header("Mobile No") == "mobile no"
    assert norm_header("preferred_language") == "preferred language"
    assert norm_header("  Insured   Name ") == "insured name"
    assert norm_header("__phone") == "phone"
    assert norm_header("policy__number") == "policy number"


def test_naive_timestamp_means_ist_and_z_means_utc():
    naive = parse_occurred_at("2026-10-03 22:30:00")
    zulu = parse_occurred_at("2026-10-03T17:00:00Z")
    offset = parse_occurred_at("2026-10-03T22:30:00+05:30")
    assert naive == zulu == offset
    assert naive.tzinfo == timezone.utc
    assert format_ist_iso(naive) == "2026-10-03T22:30:00+05:30"
    evening_utc = parse_occurred_at("2026-10-03T20:00:00Z")
    assert ist_date(evening_utc) == "2026-10-04"
    assert format_ist_iso(evening_utc) == "2026-10-04T01:30:00+05:30"
