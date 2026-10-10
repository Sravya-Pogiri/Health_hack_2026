"""Coverage now and projected.

Two measures per hospital:
  - staffed: on shift, credentialed here, not Temporarily Unavailable. Drives split-shift decisions.
  - free now: staffed and routable right now. Drives reassignment and escalation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .physicians import TEMP_UNAVAILABLE
from .timeline import World
from .timeutil import ET, at_et, fmt_clock, parse_hhmm


@dataclass
class PlannedJoin:
    """A physician who will join: an accepted offer or an agreed early start."""
    physician_id: str
    online_at: datetime
    end: datetime
    hospitals: set[str] | None      # flood guard limit; None means every credentialed hospital
    label: str


@dataclass
class CoveragePoint:
    t: datetime
    required: int
    base_required: int
    surging: bool
    staffed: list[str]
    unavailable: list[str]

    def to_dict(self) -> dict:
        return {"t": self.t.isoformat(), "required": self.required, "staffed": len(self.staffed),
                "staffed_ids": self.staffed, "surging": self.surging, "short": max(0, self.required - len(self.staffed))}


@dataclass
class Gap:
    id: str
    hospital_id: str
    kind: str                     # "now" (nobody free for a waiting consult) or "projected" (staffed below required)
    start: datetime
    end: datetime
    required: int
    staffed: int
    shortfall: int
    causes: list[str] = field(default_factory=list)
    cause_text: list[str] = field(default_factory=list)
    open_ended: bool = False
    consult_ids: list[str] = field(default_factory=list)

    def to_dict(self, hospital_names: dict[str, str]) -> dict:
        return {
            "id": self.id, "hospital_id": self.hospital_id, "hospital_name": hospital_names.get(self.hospital_id),
            "kind": self.kind, "start": self.start.isoformat(), "end": self.end.isoformat(),
            "required": self.required, "staffed": self.staffed, "shortfall": self.shortfall,
            "causes": self.causes, "cause_text": self.cause_text, "open_ended": self.open_ended,
            "consult_ids": self.consult_ids,
        }


def _block_at(ds, hospital_id: str, t: datetime):
    minute = t.astimezone(ET).hour * 60 + t.astimezone(ET).minute
    for block in ds.ref.coverage.get(hospital_id, []):
        if block.contains_minute(minute):
            return block
    return None


def base_required(ds, hospital_id: str, t: datetime) -> int:
    block = _block_at(ds, hospital_id, t)
    return block.required if block else 1


def staffed_at(ds, world: World, hospital_id: str, t: datetime,
               joins: list[PlannedJoin] = ()) -> tuple[list[str], list[str]]:
    """Physicians on shift at t and credentialed here. Status is assumed to persist into the future."""
    staffed, unavailable = [], []
    for p in world.physicians.values():
        if hospital_id not in ds.ref.credentials.get(p.id, set()):
            continue
        on = p.window_at(t) is not None
        joined = any(j.physician_id == p.id and j.online_at <= t < j.end
                     and (j.hospitals is None or hospital_id in j.hospitals) for j in joins)
        if not (on or joined):
            continue
        if p.self_status == TEMP_UNAVAILABLE:
            unavailable.append(p.id)
        else:
            staffed.append(p.id)
    return sorted(set(staffed)), sorted(set(unavailable))


def horizon(ds, now: datetime) -> datetime:
    cfg = ds.cfg
    candidates = [now + timedelta(minutes=float(cfg.get_path("coverage.projection_min_minutes", 90))),
                  now + timedelta(hours=float(cfg.get_path("coverage.projection_extend_min_hours", 3)))]
    extend_to = cfg.get_path("coverage.projection_extend_to")
    if extend_to:
        t = at_et(now.astimezone(ET).date(), parse_hhmm(extend_to))
        if t > now:
            candidates.append(t)
    return max(candidates)


def grid_times(ds, now: datetime) -> list[datetime]:
    step = timedelta(minutes=float(ds.cfg.get_path("coverage.projection_step_minutes", 15)))
    end = horizon(ds, now)
    times = [now]
    first = now.astimezone(ET).replace(second=0, microsecond=0)
    first += timedelta(minutes=(-first.minute) % int(step.total_seconds() // 60) or int(step.total_seconds() // 60))
    t = first
    while t <= end:
        times.append(t)
        t += step
    return times


def _breakpoints(ds, world: World, now: datetime, end: datetime, joins, surge_until: dict) -> list[datetime]:
    points = set(grid_times(ds, now))
    for p in world.physicians.values():
        for w in p.windows:
            for t in (w.start, w.end):
                if now < t <= end:
                    points.add(t)
    for j in joins:
        for t in (j.online_at, j.end):
            if now < t <= end:
                points.add(t)
    for until in surge_until.values():
        if until and now < until <= end:
            points.add(until)
    day = now.astimezone(ET).date()
    for blocks in ds.ref.coverage.values():
        for b in blocks:
            for offset in range(0, 3):
                t = at_et(day + timedelta(days=offset), parse_hhmm(f"{b.start_min // 60:02d}:{b.start_min % 60:02d}"))
                if now < t <= end:
                    points.add(t)
    points.add(end)
    return sorted(p for p in points if now <= p <= end)


def coverage_point(ds, world: World, hospital_id: str, t: datetime, joins, surge_until: dict) -> CoveragePoint:
    base = base_required(ds, hospital_id, t)
    until = surge_until.get(hospital_id)
    surging = bool(until and t < until)
    extra = int(ds.cfg.get_path("demand.surge_extra_required", 1)) if surging else 0
    staffed, unavailable = staffed_at(ds, world, hospital_id, t, joins)
    return CoveragePoint(t, base + extra, base, surging, staffed, unavailable)


def project(ds, world: World, surge_until: dict[str, datetime | None], joins: list[PlannedJoin] = ()) -> dict:
    """Coverage every 15 minutes to the horizon, plus projected gaps (start before the horizon)."""
    now = world.now
    end = horizon(ds, now)
    lookahead = end + timedelta(hours=float(ds.cfg.get_path("coverage.gap_end_lookahead_hours", 6)))
    grid = grid_times(ds, now)
    points = _breakpoints(ds, world, now, lookahead, joins, surge_until)
    hospitals = {}
    gaps: list[Gap] = []
    names = {h.id: h.name for h in ds.ref.hospitals.values()}
    for hid in ds.ref.hospitals:
        series = [coverage_point(ds, world, hid, t, joins, surge_until) for t in points]
        by_time = {cp.t: cp for cp in series}
        hospitals[hid] = [by_time[t].to_dict() if t in by_time else
                          coverage_point(ds, world, hid, t, joins, surge_until).to_dict() for t in grid]
        current = None
        for i, cp in enumerate(series):
            short = cp.required - len(cp.staffed)
            seg_end = series[i + 1].t if i + 1 < len(series) else lookahead
            if short > 0:
                if current is None:
                    if cp.t >= end:
                        break
                    current = Gap(f"gap-{hid}-{cp.t.astimezone(ET).strftime('%H%M')}", hid, "projected", cp.t, seg_end,
                                  cp.required, len(cp.staffed), short)
                    _explain(ds, world, current, cp, names)
                else:
                    current.end = seg_end
                    current.shortfall = max(current.shortfall, short)
                    current.staffed = min(current.staffed, len(cp.staffed))
                    current.required = max(current.required, cp.required)
            elif current is not None:
                gaps.append(current)
                current = None
        if current is not None:
            current.open_ended = True
            gaps.append(current)
    return {"times": [t.isoformat() for t in grid], "horizon": end.isoformat(), "hospitals": hospitals, "gaps": gaps}


def _names(world: World, ids) -> str:
    names = [world.physicians[i].name for i in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _explain(ds, world: World, gap: Gap, cp: CoveragePoint, hospital_names: dict) -> None:
    now = world.now
    staffed_now, _ = staffed_at(ds, world, gap.hospital_id, now)
    left = [pid for pid in staffed_now if pid not in cp.staffed]
    where = hospital_names.get(gap.hospital_id, gap.hospital_id)
    if left:
        gap.causes.append("shift_end")
        by_time: dict[datetime, list[str]] = {}
        for pid in left:
            w = world.physicians[pid].window_at(now)
            by_time.setdefault(w.end if w else cp.t, []).append(pid)
        parts = [f"{_names(world, ids)} at {fmt_clock(t)}" for t, ids in sorted(by_time.items())]
        gap.cause_text.append("Shift ends: " + "; ".join(parts) + ".")
        remaining = (f"Only {_names(world, cp.staffed)} will be on shift and credentialed here."
                     if cp.staffed else "Nobody credentialed here will be on shift.")
        gap.cause_text.append(remaining)
    if cp.surging and len(cp.staffed) >= cp.base_required:
        gap.causes.append("surge")
        gap.cause_text.append(f"A demand surge raises the requirement at {where} from {cp.base_required} to {cp.required}.")
    if cp.unavailable:
        gap.causes.append("unavailable")
        gap.cause_text.append(f"{_names(world, cp.unavailable)} is set to Temporarily Unavailable.")
    on_shift_elsewhere = [p.id for p in world.physicians.values()
                          if p.window_at(cp.t) and p.id not in cp.staffed and p.id not in cp.unavailable]
    if on_shift_elsewhere:
        gap.causes.append("credentialing")
        gap.cause_text.append(f"{len(on_shift_elsewhere)} other doctor{'s are' if len(on_shift_elsewhere) != 1 else ' is'} "
                              f"on shift then but not credentialed at {where}.")
    if not gap.cause_text:
        gap.cause_text.append(f"Only {len(cp.staffed)} credentialed doctor{'s' if len(cp.staffed) != 1 else ''} "
                              f"on shift against a requirement of {cp.required}.")
