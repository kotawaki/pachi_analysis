"""Production calendar helpers for explicitly closed store dates."""

from __future__ import annotations

import json
from datetime import date as Date, timedelta
from pathlib import Path

from .inputs import normalize_date


CALENDAR_FILENAME = "calendar.json"


def load_closed_dates(data_root: str | Path) -> frozenset[str]:
    """Load closed dates without making a missing calendar an error."""
    path = Path(data_root) / CALENDAR_FILENAME
    if not path.exists():
        return frozenset()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    values = payload.get("closed_dates", []) if isinstance(payload, dict) else []
    normalized = set()
    for value in values if isinstance(values, list) else []:
        try:
            normalized.add(normalize_date(str(value)))
        except ValueError:
            continue
    return frozenset(normalized)


def next_non_closed_date(value: str, closed_dates: frozenset[str] | set[str]) -> str:
    """Return the next calendar date not listed as closed."""
    parsed = Date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    while True:
        parsed += timedelta(days=1)
        candidate = parsed.strftime("%Y%m%d")
        if candidate not in closed_dates:
            return candidate
