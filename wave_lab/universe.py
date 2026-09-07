"""Date-aware production universes.

The historical universe is intentionally kept separate so that changing the
current machine layout cannot rewrite past observations.
"""

CURRENT_START_DATE = "20260907"
LEGACY_WAVE_MACHINES = tuple(f"{n:03d}" for n in range(39, 78))
CURRENT_WAVE_MACHINES = ("018", "019", *LEGACY_WAVE_MACHINES)


def machines_for_signal_date(signal_date: str) -> tuple[str, ...]:
    """Return the Wave universe applicable on the signal date."""
    compact = str(signal_date).replace("-", "")
    return CURRENT_WAVE_MACHINES if compact >= CURRENT_START_DATE else LEGACY_WAVE_MACHINES


def is_protected_holiday(value: str) -> bool:
    return str(value).replace("-", "") == "20260827"
