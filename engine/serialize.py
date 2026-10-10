"""JSON-ready views of engine state, shared by the API and the mock generator."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .consults import ConsultState
from .dataset import Dataset
from .physicians import PhysicianState
from .timeline import World
from .timeutil import fmt_clock, iso, minutes_between

ACTIVITY_WORDS = {"video": "On video", "incoming_call": "On incoming call", "outgoing_call": "On outgoing call",
                  "note": "Writing note"}
PLAIN_STATES = {"Camera Active - No Synapse Session": "Camera on, no Synapse video",
                "Temporarily Unavailable": "Set to temporarily unavailable",
                "In Meeting": "In an internal meeting",
                "Off Shift": "Off shift"}


def hospital_local_time(ds: Dataset, hospital_id: str, t: datetime) -> str:
    h = ds.ref.hospitals.get(hospital_id)
    if not h:
        return ""
    local = t.astimezone(ZoneInfo(h.timezone))
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} {local.tzname()}"


def physician_json(ds: Dataset, world: World, p: PhysicianState) -> dict:
    now = world.now
    hospitals = ds.ref.hospitals
    activity_text = "Idle"
    if p.current_activity:
        a = p.current_activity
        hosp = hospitals.get(a.get("hospital_id") or "", None)
        if hosp is None and a.get("consult_id") in world.consults:
            hosp = hospitals.get(world.consults[a["consult_id"]].hospital_id)
        parts = [ACTIVITY_WORDS.get(a["kind"], a["kind"])]
        if a.get("consult_id"):
            parts.append(a["consult_id"])
        if hosp:
            parts.append(hosp.name)
        activity_text = ", ".join(parts)
    elif p.derived_state in PLAIN_STATES:
        activity_text = PLAIN_STATES[p.derived_state]
    return {
        "id": p.id,
        "name": p.name,
        "on_shift": p.on_shift,
        "shift": ({"start": iso(p.shift.start), "end": iso(p.shift.end), "kind": p.shift.kind,
                   "label": f"{fmt_clock(p.shift.start)} to {fmt_clock(p.shift.end)} ET"} if p.shift else None),
        "minutes_to_shift_end": p.minutes_to_shift_end,
        "self_reported_status": p.self_status,
        "desktop_agent": "Online" if p.agent_online else "Offline",
        "derived_state": p.derived_state,
        "activity_text": activity_text,
        "verdict": {"code": p.verdict, "label": p.verdict_label, "routable": p.routable, "reason": p.verdict_reason},
        "flags": p.flags,
        "consults_this_shift": p.consults_this_shift,
        "calls_this_shift": p.calls_this_shift,
        "open_consults": p.open_consult_ids,
        "last_signal_minutes_ago": minutes_between(p.last_signal_at, now) if p.last_signal_at else None,
        "credentialed_hospitals": sorted(ds.ref.credentials.get(p.id, set())),
    }


def consult_json(ds: Dataset, world: World, c: ConsultState) -> dict:
    now = world.now
    hosp = ds.ref.hospitals.get(c.hospital_id)
    phys = world.physicians.get(c.physician_id or "")
    return {
        "id": c.consult_id,
        "hospital_id": c.hospital_id,
        "hospital_name": hosp.name if hosp else c.hospital_id,
        "hospital_local_time": hospital_local_time(ds, c.hospital_id, now),
        "physician_id": c.physician_id,
        "physician_name": phys.name if phys else None,
        "type": c.consult_type,
        "type_known": c.type_known,
        "stage": c.stage,
        "stage_entered_at": iso(c.stage_entered_at),
        "minutes_in_stage": minutes_between(c.stage_entered_at, now) if c.stage_entered_at else None,
        "received_at": iso(c.received_at),
        "minutes_since_received": minutes_between(c.received_at, now) if c.received_at else None,
        "clinical_end_at": iso(c.clinical_end_at),
        "phone_only": c.phone_only,
        "missing_received": c.missing_received,
        "recall_count": c.recall_count,
        "last_recall_at": iso(c.last_recall_at),
        "routing_failures": len(c.routing_failures),
        "last_routing_failure_at": iso(c.routing_failures[-1]) if c.routing_failures else None,
        "ringing_to": c.ringing_to,
        "is_open": c.is_open,
    }


def data_health_json(ds: Dataset, world: World) -> dict:
    now = world.now
    late = [e for r in ds.log.reports for e in r.late_events]
    dups = sum(len(r.duplicates) for r in ds.log.reports)
    skipped = [s for r in ds.log.reports for s in r.skipped]
    unknown_p = sorted({p for r in ds.log.reports for p in r.unknown_physicians})
    unknown_h = sorted({h for r in ds.log.reports for h in r.unknown_hospitals})
    stale_after = float(ds.cfg.get_path("alerts.stale_data_minutes", 10))
    age = minutes_between(world.last_ingested_at, now) if world.last_ingested_at else None
    return {
        "data_as_of": iso(world.last_ingested_at),
        "last_record_minutes_ago": age,
        "stale": age is not None and age >= stale_after,
        "events_loaded": len(ds.events),
        "events_applied": world.events_applied,
        "events_pending": world.events_pending,
        "duplicates_dropped": dups,
        "late_events": late,
        "skipped_rows": skipped,
        "unknown_physicians": unknown_p,
        "unknown_hospitals": unknown_h,
        "agent_offline": [p.id for p in world.physicians.values() if p.on_shift and not p.agent_online],
        "files": [r.source for r in ds.log.reports],
        "warnings": ds.load_report.warnings,
    }


def state_json(ds: Dataset, world: World) -> dict:
    """Milestone 1 subset of GET /api/state. Issues, coverage, and offers are added in later milestones."""
    return {
        "now": iso(world.now),
        "now_label": f"{fmt_clock(world.now)} ET",
        "scenario_date": ds.ref.scenario_date.isoformat() if ds.ref.scenario_date else None,
        "physicians": [physician_json(ds, world, p) for p in sorted(world.physicians.values(), key=lambda p: p.id)],
        "consults": [consult_json(ds, world, c) for c in sorted(world.consults.values(), key=lambda c: c.consult_id)
                     if c.is_open],
        "data_health": data_health_json(ds, world),
    }
