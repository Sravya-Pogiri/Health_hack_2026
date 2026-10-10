"""Routing simulator: models the effect of Sevaro's five synthetic routing rules.

It does not replace the production engine. It answers "who would get the next
call at this hospital, and why was everyone else skipped?"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .timeline import World


@dataclass
class Candidate:
    id: str
    name: str
    credentials: set[str]
    on_shift: bool
    consults: int                       # consults this shift (possibly seeded by the flood guard)
    last_answered_at: datetime | None
    night: bool
    blocker: str | None = None          # why routing rule 2 removes them, if it does
    allowed_hospitals: set[str] | None = None   # flood guard: joiner limited to these hospitals


@dataclass
class RouteResult:
    hospital_id: str
    chosen: str | None
    chosen_name: str | None
    steps: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"hospital_id": self.hospital_id, "physician_id": self.chosen, "name": self.chosen_name,
                "reason": self.reason, "steps": self.steps}


def is_night(p, cfg, role: str | None = None) -> bool:
    """Night threshold applies to night-role physicians and to shifts starting at or after the night hour."""
    if (role or "").lower() == "night":
        return True
    night_hour = int(cfg.get_path("shifts.night_start_hour", 19))
    return bool(p.shift and p.shift.start.hour >= night_hour)


def routing_blocker(p, world: World, cfg) -> str | None:
    """Routing rule 2 for a real physician: busy, phone ringing, cooling down, or unavailable."""
    if not p.on_shift:
        return "Not on shift."
    if not p.routable:
        return p.verdict_reason
    if cfg.get_path("routing.pending_contact_blocks", True):
        pending = [cid for cid in p.open_consult_ids if world.consults[cid].stage == "Assigned"]
        if pending:
            return (f"Waiting for the hospital's call on {pending[0]}, which was assigned and not yet contacted. "
                    "Treated as a phone about to ring.")
    return None


def candidates_from_world(ds, world: World) -> list[Candidate]:
    cfg = ds.cfg
    out = []
    for p in world.physicians.values():
        out.append(Candidate(
            id=p.id, name=p.name, credentials=set(ds.ref.credentials.get(p.id, set())), on_shift=p.on_shift,
            consults=p.consults_this_shift, last_answered_at=p.last_answered_at,
            night=is_night(p, cfg, ds.ref.physicians[p.id].role if p.id in ds.ref.physicians else None),
            blocker=routing_blocker(p, world, cfg),
        ))
    return out


def route_core(cands: list[Candidate], hospital_id: str, cfg, hospital_name: str = "") -> RouteResult:
    """Apply routing rules 1 to 4. Rule 5 (retry every 5 minutes) is the caller's loop."""
    where = hospital_name or hospital_id
    steps = []

    on_shift = [c for c in cands if c.on_shift]
    rule1, removed = [], []
    for c in on_shift:
        if hospital_id not in c.credentials:
            removed.append({"physician_id": c.id, "reason": f"Not credentialed at {where}."})
        elif c.allowed_hospitals is not None and hospital_id not in c.allowed_hospitals:
            removed.append({"physician_id": c.id,
                            "reason": f"Flood guard: joined to cover other hospitals, so kept off {where} for now."})
        else:
            rule1.append(c)
    steps.append({"rule": 1, "title": "On shift and credentialed here", "kept": [c.id for c in rule1],
                  "removed": removed})

    rule2, removed = [], []
    for c in rule1:
        if c.blocker:
            removed.append({"physician_id": c.id, "reason": c.blocker})
        else:
            rule2.append(c)
    steps.append({"rule": 2, "title": "Not busy, not ringing, not in the 2-minute cooldown", "kept": [c.id for c in rule2],
                  "removed": removed})
    if not rule2:
        reason = (f"No one: nobody credentialed at {where} is on shift." if not rule1 else
                  f"No one: every credentialed physician at {where} is busy or unavailable.")
        return RouteResult(hospital_id, None, None, steps, reason)

    day_max = int(cfg.get_path("routing.low_volume_day", 6))
    night_max = int(cfg.get_path("routing.low_volume_night", 4))
    low = [c for c in rule2 if c.consults <= (night_max if c.night else day_max)]
    pool = low or rule2
    steps.append({"rule": 3, "title": f"Low volume first ({day_max} or fewer on days, {night_max} or fewer on nights)",
                  "kept": [c.id for c in pool], "removed": [], "applied": bool(low)})

    def tie_key(c: Candidate):
        # No answered call yet counts as least recent.
        return (c.consults, c.last_answered_at.timestamp() if c.last_answered_at else float("-inf"), c.id)

    pool = sorted(pool, key=tie_key)
    chosen = pool[0]
    steps.append({"rule": 4, "title": "Fewest consults this shift; ties go to whoever answered least recently",
                  "order": [{"physician_id": c.id, "consults": c.consults,
                             "last_answered_at": c.last_answered_at.isoformat() if c.last_answered_at else None}
                            for c in pool]})
    reason = (f"{chosen.name}: {'low volume, ' if low else ''}{chosen.consults} consults this shift, "
              f"the fewest among {len(pool)} eligible.")
    return RouteResult(hospital_id, chosen.id, chosen.name, steps, reason)


def route(ds, world: World, hospital_id: str, exclude: set[str] | None = None) -> RouteResult:
    cands = [c for c in candidates_from_world(ds, world) if not exclude or c.id not in exclude]
    hosp = ds.ref.hospitals.get(hospital_id)
    return route_core(cands, hospital_id, ds.cfg, hosp.name if hosp else hospital_id)


def next_up_all(ds, world: World) -> dict[str, RouteResult]:
    cands = candidates_from_world(ds, world)
    return {hid: route_core(cands, hid, ds.cfg, h.name) for hid, h in ds.ref.hospitals.items()}
