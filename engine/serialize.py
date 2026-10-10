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
    elif p.ringing:
        hosp = hospitals.get(p.ringing.get("hospital_id") or "")
        activity_text = ", ".join(x for x in ["Phone ringing", p.ringing.get("consult_id"), hosp.name if hosp else ""] if x)
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


def load_json(world: World, p: PhysicianState, cfg) -> dict:
    """Load meter: consults this shift against the team average and a soft sustainable limit."""
    on = [q.consults_this_shift for q in world.physicians.values() if q.on_shift]
    avg = sum(on) / len(on) if on else 0
    limit = int(cfg.get_path("load.soft_consult_limit", 15))
    n = p.consults_this_shift
    level = ("at or over the soft limit" if n >= limit else "above team average" if avg and n > avg * 1.15 else
             "below team average" if avg and n < avg * 0.6 else "typical")
    return {"consults": n, "team_average": round(avg, 1), "soft_limit": limit, "level": level}


def hospital_json(ds: Dataset, an, hid: str) -> dict:
    h = ds.ref.hospitals[hid]
    cov = an.coverage_now[hid]
    nu = an.next_up.get(hid)
    surge = an.surges[hid]
    return {
        "id": hid, "name": h.name, "tier": h.tier, "timezone": h.timezone,
        "local_time": hospital_local_time(ds, hid, an.world.now),
        "required_now": cov["required"], "base_required": cov["base_required"],
        "staffed_now": len(cov["staffed"]), "staffed_ids": cov["staffed"],
        "free_now": len(cov["free"]), "free_ids": cov["free"],
        "unavailable_ids": cov["unavailable"], "waiting_consults": cov["waiting"],
        "surge": {**surge.to_dict(), "until": iso(an.surge_until.get(hid))},
        "next_up": {"physician_id": nu.chosen, "name": nu.chosen_name, "reason": nu.reason} if nu else None,
        "promise_at_risk": bool(nu and not nu.chosen),
        "next_change": an.next_change.get(hid, ""),
        "gap_ids": [g.id for g in an.all_gaps if g.hospital_id == hid],
    }


def analysis_json(ds: Dataset, an) -> dict:
    """GET /api/state: everything the three role views need."""
    world = an.world
    names = {h.id: h.name for h in ds.ref.hospitals.values()}
    physicians = []
    for p in sorted(world.physicians.values(), key=lambda p: p.id):
        item = physician_json(ds, world, p)
        fi = an.free_in.get(p.id)
        item["free_in_minutes"] = fi.minutes if fi else None
        item["free_in_basis"] = fi.basis if fi else None
        item["long_case"] = bool(fi and fi.long_case)
        item["load"] = load_json(world, p, ds.cfg) if p.on_shift else None
        notes = an.open_notes.get(p.id)
        item["open_notes_near_shift_end"] = ({"status": notes.status, "consult_ids": notes.consult_ids}
                                             if notes else None)
        physicians.append(item)
    consults = []
    for c in sorted(world.consults.values(), key=lambda c: c.consult_id):
        if not c.is_open:
            continue
        item = consult_json(ds, world, c)
        chk = an.standards.get(c.consult_id)
        item["standard"] = chk.to_dict() if chk else None
        consults.append(item)
    groups = [g.to_dict() for g in an.groups]
    flat = [i for g in groups for i in g["issues"]]
    return {
        "now": iso(world.now),
        "now_label": f"{fmt_clock(world.now)} ET",
        "scenario_date": ds.ref.scenario_date.isoformat() if ds.ref.scenario_date else None,
        "physicians": physicians,
        "consults": consults,
        "hospitals": [hospital_json(ds, an, hid) for hid in sorted(ds.ref.hospitals)],
        "issues": groups,
        "gaps": [g.to_dict(names) for g in an.all_gaps],
        "projection": {"times": an.projection["times"], "horizon": an.projection["horizon"],
                       "hospitals": an.projection["hospitals"]},
        "eligibility": {
            "now": an.cluster.to_dict(names) if an.cluster else None,
            "by_gap": {gid: r.to_dict(names) for gid, r in an.gap_eligibility.items()},
        },
        "data_health": data_health_json(ds, world),
        "summary": {
            "critical": sum(1 for i in flat if i["severity"] == "critical"),
            "warning": sum(1 for i in flat if i["severity"] == "warning"),
            "projected": sum(1 for i in flat if i["severity"] == "projected"),
            "stroke_critical": sum(1 for i in flat if i["severity"] == "critical" and i["stroke"]),
        },
    }


def state_json(ds: Dataset, world: World) -> dict:
    from .analysis import analyze
    return analysis_json(ds, analyze(ds, world))
