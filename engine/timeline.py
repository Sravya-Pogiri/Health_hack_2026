"""Replay: rebuild the whole world at any time t from the ordered event log.

An event counts at time t only if it had both happened (event_time <= t) and
arrived (ingested_at <= t). Rebuilding from scratch every time is cheap and
handles late events, duplicates, and appended files the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .consults import ConsultState, apply_consult_event
from .dataset import Dataset
from .model import ShiftWindow
from .physicians import PhysicianState, apply_physician_event, finalize_physician, init_physician_states


@dataclass
class World:
    now: datetime
    consults: dict[str, ConsultState]
    physicians: dict[str, PhysicianState]
    events_applied: int = 0
    events_pending: int = 0          # loaded but not yet happened or arrived at `now`
    last_event_time: datetime | None = None
    last_ingested_at: datetime | None = None
    notes: list[str] = field(default_factory=list)


def replay(ds: Dataset, now: datetime, joins=()) -> World:
    """joins: PlannedJoin records (accepted offers, agreed early starts) that add shift windows."""
    consults: dict[str, ConsultState] = {}
    physicians = init_physician_states(ds.ref.physicians)
    for j in joins:
        p = physicians.get(j.physician_id)
        if p is None:
            continue
        if j.kind == "start_early":
            # Move the start of the scheduled shift earlier rather than adding a second window.
            later = [w for w in p.windows if w.start >= j.online_at]
            if later:
                w = min(later, key=lambda w: w.start)
                w.start, w.source = j.online_at, j.label
                continue
        p.windows.append(ShiftWindow(j.online_at, j.end, "split_shift", j.hospitals, j.label))
    aliases = ds.log.consult_aliases
    applied = pending = 0
    last_event = last_ingested = None
    for ev in ds.events:
        if ev.event_time > now or ev.ingested_at > now:
            pending += 1
            continue
        apply_consult_event(consults, ev, aliases)
        apply_physician_event(physicians, ev, ds.cfg)
        applied += 1
        last_event = ev.event_time if last_event is None else max(last_event, ev.event_time)
        last_ingested = ev.ingested_at if last_ingested is None else max(last_ingested, ev.ingested_at)
    for p in physicians.values():
        finalize_physician(p, now, consults, ds.cfg)
    return World(now, consults, physicians, applied, pending, last_event, last_ingested)
