"""Pure parsers for lead cells, headers, and event timestamps.

No database access. These are the rules most likely to be changed in review.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

IST = timezone(timedelta(hours=5, minutes=30))

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# ₹, Rs, Rs., or INR, then an optional space. Matched only at the start.
_MONEY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DMY_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_DMY_DASH = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_DMON_DATE = re.compile(r"^(\d{2})-([A-Za-z]{3})-(\d{4})$")


def norm_header(header: str) -> str:
    """Lower-case, trim, and collapse runs of whitespace or underscores.

    A final trim removes a space left behind by a leading underscore run,
    so a header like ``__phone`` still matches ``phone``.
    """
    lowered = header.strip().lower()
    return re.sub(r"[\s_]+", " ", lowered).strip()


def canonical_phone(text: str) -> str | None:
    """Return ``+91`` plus 10 digits, or None when the text is not a valid mobile.

    Empty input is not valid. Callers that need to tell "missing" from "invalid"
    should check for a blank string before calling this.
    """
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


def parse_date(text: str) -> str | None:
    """Parse a day-first date to ``YYYY-MM-DD``, or None if it is impossible."""
    raw = text.strip()
    match = _ISO_DATE.fullmatch(raw)
    if match:
        return _valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = _DMY_SLASH.fullmatch(raw)
    if match:
        return _valid_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    match = _DMY_DASH.fullmatch(raw)
    if match:
        return _valid_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    match = _DMON_DATE.fullmatch(raw)
    if match:
        month = _MONTHS.get(match.group(2).lower())
        if month is None:
            return None
        return _valid_date(int(match.group(3)), month, int(match.group(1)))
    return None


def _valid_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_decimal(text: str) -> str | None:
    """Parse a non-negative money amount and round half up to 2 places."""
    cleaned = _MONEY_PREFIX.sub("", text.strip())
    cleaned = cleaned.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{quantized:.2f}"


def parse_enum(text: str, values: list[str]) -> str | None:
    """Case-insensitive enum match. The stored form is lower-case."""
    key = text.strip().lower()
    allowed = {value.lower() for value in values}
    if key in allowed:
        return key
    return None


def parse_occurred_at(raw: str | None) -> datetime | None:
    """Parse an event timestamp to an aware UTC datetime.

    A trailing ``Z`` is UTC. An explicit offset is honoured. A naive timestamp
    means India Standard Time (UTC+05:30).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_ist_iso(moment: datetime) -> str:
    """ISO-8601 in IST with seconds, for example ``2026-10-14T17:55:24+05:30``."""
    return moment.astimezone(IST).isoformat(timespec="seconds")


def ist_date(moment: datetime) -> str:
    return moment.astimezone(IST).date().isoformat()
