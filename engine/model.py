"""Typed records for reference data and events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Hospital:
    id: str
    name: str
    timezone: str
    tier: str


@dataclass
class ShiftWindow:
    start: datetime
    end: datetime
    kind: str  # "scheduled" (on-shift file), "next_scheduled" (roster), "joined" (shift_started event), "split_shift"
    hospitals: set[str] | None = None   # split shift: the hospitals the offer covers (flood guard limit)
    source: str | None = None           # offer id for split shifts and early starts

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


@dataclass
class Physician:
    id: str
    name: str
    in_on_shift_file: bool = False
    in_roster: bool = False
    role: str | None = None                 # Day, Night, Float
    opt_in: bool | None = None              # split-shift opt-in (roster only)
    last_shift_end: datetime | None = None
    next_shift_start: datetime | None = None
    hours_worked_last_7d: float | None = None
    est_minutes_to_online: int | None = None
    preferred_channel: str | None = None
    offers_declined_last_30d: int | None = None
    scheduled_shifts: list[ShiftWindow] = field(default_factory=list)


@dataclass
class BaselineRow:
    hospital_id: str
    hour: int
    mean: float
    p90: float
    weeks: int | None


@dataclass
class CoverageBlock:
    hospital_id: str
    start_min: int   # minutes after midnight ET
    end_min: int     # may be <= start_min when the block wraps midnight
    required: int
    label: str

    def contains_minute(self, minute_of_day: int) -> bool:
        if self.start_min < self.end_min:
            return self.start_min <= minute_of_day < self.end_min
        return minute_of_day >= self.start_min or minute_of_day < self.end_min


@dataclass(frozen=True)
class Event:
    event_id: str
    event_time: datetime
    ingested_at: datetime
    consult_id: str
    event_type: str
    physician_id: str
    hospital_id: str
    description: str
    source: str = "kit"
    seq: int = 0          # arrival order across all loaded files

    @property
    def lag_seconds(self) -> float:
        return (self.ingested_at - self.event_time).total_seconds()
