"""Timezone-aware time helpers. Internal logic runs in Eastern time."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp. Raises ValueError if malformed.

    Timestamps without an offset are assumed to be Eastern time.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def parse_hhmm(value: str) -> time:
    text = (value or "").strip()
    parts = text.split(":")
    if len(parts) < 2:
        raise ValueError(f"bad HH:MM value: {value!r}")
    return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)


def at_et(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=ET)


def minutes_between(earlier: datetime, later: datetime) -> int:
    """Whole minutes from earlier to later, floored (the snapshot floors minutes)."""
    return int((later - earlier).total_seconds() // 60)


def fmt_clock(dt: datetime | None) -> str:
    """6:20 PM style, Eastern."""
    if dt is None:
        return ""
    local = dt.astimezone(ET)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def fmt_hms(dt: datetime | None) -> str:
    return dt.astimezone(ET).strftime("%H:%M:%S") if dt else ""


def fmt_hm(dt: datetime | None) -> str:
    return dt.astimezone(ET).strftime("%H:%M") if dt else ""


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(ET).isoformat() if dt else None


def floor_to_hour(dt: datetime) -> datetime:
    local = dt.astimezone(ET)
    return local.replace(minute=0, second=0, microsecond=0)


def add_minutes(dt: datetime, minutes: float) -> datetime:
    return dt + timedelta(minutes=minutes)


def join_names(names: list[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def fmt_day_clock(dt: datetime, ref: datetime) -> str:
    """6:20 PM, with the date added when it is not the same day as ref."""
    local, base = dt.astimezone(ET), ref.astimezone(ET)
    if local.date() == base.date():
        return fmt_clock(dt)
    return f"{local.strftime('%b')} {local.day} {fmt_clock(dt)}"
