"""Exports the world in the exact column format of the reference snapshot files."""
from __future__ import annotations

from .timeline import World
from .timeutil import fmt_hm, fmt_hms, minutes_between

PHYSICIAN_COLUMNS = [
    "physician_id", "name", "shift_et", "self_reported_status", "desktop_agent", "derived_state",
    "consults_this_shift", "calls_this_shift", "open_consults", "minutes_to_shift_end",
]
CONSULT_COLUMNS = [
    "consult_id", "hospital_id", "hospital_name", "physician_id", "consult_type", "current_stage",
    "stage_entered_at", "minutes_in_stage", "minutes_since_received",
]


def physician_rows(world: World) -> list[dict]:
    rows = []
    for pid in sorted(world.physicians):
        p = world.physicians[pid]
        if not p.on_shift:
            continue
        rows.append({
            "physician_id": pid,
            "name": p.name,
            "shift_et": f"{fmt_hm(p.shift.start)}-{fmt_hm(p.shift.end)}",
            "self_reported_status": p.self_status,
            "desktop_agent": "Online" if p.agent_online else "Offline",
            "derived_state": p.derived_state,
            "consults_this_shift": str(p.consults_this_shift),
            "calls_this_shift": str(p.calls_this_shift),
            "open_consults": str(len(p.open_consult_ids)),
            "minutes_to_shift_end": str(p.minutes_to_shift_end),
        })
    return rows


def consult_rows(world: World, hospital_names: dict[str, str]) -> list[dict]:
    rows = []
    for cid in sorted(world.consults):
        c = world.consults[cid]
        if not c.is_open:
            continue
        rows.append({
            "consult_id": cid,
            "hospital_id": c.hospital_id,
            "hospital_name": hospital_names.get(c.hospital_id, c.hospital_id),
            "physician_id": c.physician_id or "Unassigned",
            "consult_type": c.consult_type,
            "current_stage": c.stage,
            "stage_entered_at": fmt_hms(c.stage_entered_at),
            "minutes_in_stage": str(minutes_between(c.stage_entered_at, world.now)),
            "minutes_since_received": str(minutes_between(c.received_at, world.now)),
        })
    return rows
