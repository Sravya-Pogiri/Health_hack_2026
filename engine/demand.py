"""Demand and surge: each hospital is compared with its own normal level, never a fixed number."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .consults import ConsultState
from .timeutil import ET


@dataclass
class SurgeStatus:
    hospital_id: str
    at: datetime
    active: bool
    trailing_count: int
    p90: float | None
    mean: float | None
    hour: int
    consult_ids: list[str] = field(default_factory=list)
    baseline_missing: bool = False

    @property
    def multiple(self) -> float | None:
        if not self.mean:
            return None
        return round(self.trailing_count / self.mean, 1)

    def to_dict(self) -> dict:
        return {
            "active": self.active, "trailing_count": self.trailing_count, "p90": self.p90, "mean": self.mean,
            "multiple": self.multiple, "hour": self.hour, "consult_ids": self.consult_ids,
            "baseline_missing": self.baseline_missing,
        }


def trailing_consults(consults: dict[str, ConsultState], hospital_id: str, t: datetime, window_minutes: float) -> list[str]:
    """Distinct consults first received in (t - window, t]. Re-calls never create consults, so they never count."""
    start = t - timedelta(minutes=window_minutes)
    return sorted(cid for cid, c in consults.items()
                  if c.hospital_id == hospital_id and c.received_at and start < c.received_at <= t)


def surge_at(ref, consults: dict[str, ConsultState], hospital_id: str, t: datetime, cfg) -> SurgeStatus:
    window = float(cfg.get_path("demand.trailing_window_minutes", 60))
    ids = trailing_consults(consults, hospital_id, t, window)
    hour = t.astimezone(ET).hour
    row = ref.baseline.get((hospital_id, hour))
    if row is None:
        return SurgeStatus(hospital_id, t, False, len(ids), None, None, hour, ids, baseline_missing=True)
    return SurgeStatus(hospital_id, t, len(ids) > row.p90, len(ids), row.p90, row.mean, hour, ids)


def surge_persists_until(status: SurgeStatus, cfg) -> datetime | None:
    """For projections, a surge is assumed to last for the rest of its hour plus a hold time."""
    if not status.active:
        return None
    hold = float(cfg.get_path("demand.surge_hold_minutes", 60))
    end_of_hour = status.at.astimezone(ET).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return end_of_hour + timedelta(minutes=hold)
