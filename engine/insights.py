"""Standouts: replay scoreboard, time to doctor, credentialing impact, and handoff cards.

The scoreboard reruns the whole engine every 5 minutes of the day using only what had arrived by then,
and reports when Synapp would first have flagged each problem. It reports detection lead time and when
help could have arrived. It never claims patient outcomes.
"""
from __future__ import annotations

from datetime import timedelta
from statistics import median

from .analysis import analyze
from .routing import routing_blocker
from .timeline import replay
from .timeutil import ET, fmt_clock, minutes_between


def scoreboard(ds, now, step_minutes: int = 5) -> dict:
    events = [e for e in ds.events if e.ingested_at <= now]
    if not events:
        return {"headlines": [], "items": []}
    start = min(e.event_time for e in events).astimezone(ET).replace(minute=0, second=0, microsecond=0)
    first_surge: dict[str, object] = {}
    first_gap: dict[tuple, object] = {}
    first_flag: dict[str, object] = {}
    first_critical: dict[str, object] = {}
    first_rec = None
    episode: dict[str, object] = {}          # hospital -> start of its current surge episode
    rec_since = None                         # (time, physician) the current recommendation started
    t = start
    names = {h.id: h.name for h in ds.ref.hospitals.values()}
    while t <= now:
        an = analyze(ds, replay(ds, t))
        for hid, s in an.surges.items():
            if s.active and hid not in episode:
                episode[hid] = (t, s.trailing_count, s.p90)
            elif not s.active:
                episode.pop(hid, None)
        for g in an.gaps:
            key = (g.hospital_id, g.start)
            first_gap.setdefault(key, t)
        for i in an.issues:
            cid = i.where.get("consult_id")
            if cid and i.stroke and "," not in cid:
                first_flag.setdefault(cid, (t, i.severity))
                if i.severity == "critical":
                    first_critical.setdefault(cid, t)
        best = an.cluster.best if an.cluster else None
        if best and (rec_since is None or rec_since[1] != best.physician_id):
            rec_since = (t, best.physician_id, best.name, best.offer.online_at)
        elif not best:
            rec_since = None
        t += timedelta(minutes=step_minutes)

    first_surge = episode
    first_rec = (rec_since[0], rec_since[2], rec_since[3]) if rec_since else None
    final = analyze(ds, replay(ds, now))
    recent = now - timedelta(hours=3)
    items, headlines = [], []
    for hid, (t0, count, p90) in sorted(first_surge.items(), key=lambda kv: kv[1][0]):
        lead = minutes_between(t0, now)
        items.append({"kind": "surge", "hospital": names.get(hid, hid), "first_flagged": t0.isoformat(),
                      "text": f"Surge at {names.get(hid, hid)} flagged at {fmt_clock(t0)} ({count} consults vs a "
                              f"normal busy level of {p90:g}), {lead} minutes before {fmt_clock(now)}."})
    for (hid, gstart), t0 in sorted(first_gap.items(), key=lambda kv: kv[0][1]):
        if gstart < now - timedelta(hours=1):
            continue
        items.append({"kind": "gap", "hospital": names.get(hid, hid), "first_flagged": t0.isoformat(),
                      "text": f"{names.get(hid, hid)} coverage gap at {fmt_clock(gstart)} first projected at "
                              f"{fmt_clock(t0)}, {minutes_between(t0, gstart)} minutes ahead."})
    for cid, (t0, sev) in sorted(first_flag.items(), key=lambda kv: kv[1][0]):
        if t0 < recent:
            continue
        c = final.world.consults.get(cid)
        crit = first_critical.get(cid)
        if crit and crit > t0:
            text = (f"{cid} ({names.get(c.hospital_id, '') if c else ''}) flagged at {fmt_clock(t0)}, "
                    f"{minutes_between(t0, crit)} minutes before it went critical at {fmt_clock(crit)}.")
        elif crit:
            text = f"{cid} ({names.get(c.hospital_id, '') if c else ''}) flagged at {fmt_clock(t0)} as critical."
        else:
            continue
        items.append({"kind": "consult", "consult_id": cid, "first_flagged": t0.isoformat(),
                      "critical_at": crit.isoformat() if crit else None, "text": text})
    for hid, (t0, count, p90) in sorted(first_surge.items(), key=lambda kv: kv[1][0])[:1]:
        headlines.append(f"Synapp would have flagged the {names.get(hid, hid)} surge at {fmt_clock(t0)}, "
                         f"{minutes_between(t0, now)} minutes before {fmt_clock(now)}.")
    if first_rec:
        t0, name, online = first_rec
        headlines.append(f"Synapp would have recommended {name} at {fmt_clock(t0)}; with approval they could have been "
                         f"online by about {fmt_clock(max(online, t0 + timedelta(minutes=15)))}.")
    gaps = [i for i in items if i["kind"] == "gap"]
    if gaps:
        headlines.append(f"The {gaps[0]['text']} The shift ends behind it were in the schedule all day.")
    leads = [minutes_between(datetime_or(i["first_flagged"]), datetime_or(i["critical_at"]))
             for i in items if i["kind"] == "consult" and i.get("critical_at") and i["first_flagged"] < i["critical_at"]]
    if leads:
        headlines.append(f"Stroke consults that went critical were flagged a median of {median(leads):.0f} minutes "
                         f"earlier ({len(leads)} consults).")
    return {"step_minutes": step_minutes, "from": start.isoformat(), "to": now.isoformat(), "headlines": headlines,
            "items": items,
            "note": "Detection lead time only. Synapp does not claim patient outcomes from a replay."}


def datetime_or(s):
    from datetime import datetime

    return datetime.fromisoformat(s)


def time_to_doctor(ds, world) -> dict:
    """Minutes from the first request to first contact with a doctor, and to video for stroke, per hospital."""
    names = {h.id: h.name for h in ds.ref.hospitals.values()}
    by_h: dict[str, dict[str, list]] = {}
    for c in world.consults.values():
        if not c.received_at:
            continue
        d = by_h.setdefault(c.hospital_id, {"contact": [], "video": [], "imaging_to_video": []})
        if c.contacted_at:
            d["contact"].append((c.contacted_at - c.received_at).total_seconds() / 60)
        if c.is_stroke and c.video_started_at:
            d["video"].append((c.video_started_at - c.received_at).total_seconds() / 60)
            if c.imaging_returned_at:
                d["imaging_to_video"].append(
                    (c.video_started_at - c.imaging_returned_at).total_seconds() / 60)
    rows = []
    for hid in sorted(by_h):
        d = by_h[hid]
        rows.append({"hospital_id": hid, "hospital": names.get(hid, hid), "consults": len(d["contact"]),
                     "median_to_contact": round(median(d["contact"]), 1) if d["contact"] else None,
                     "median_stroke_to_video": round(median(d["video"]), 1) if d["video"] else None,
                     "stroke_videos": len(d["imaging_to_video"]),
                     "stroke_video_warnings": sum(x >= 6 for x in d["imaging_to_video"]),
                     "stroke_video_criticals": sum(x >= 12 for x in d["imaging_to_video"]),
                     "median_stroke_imaging_to_video":
                         round(median(d["imaging_to_video"]), 1) if d["imaging_to_video"] else None})
    all_contact = [x for d in by_h.values() for x in d["contact"]]
    return {"hospitals": rows, "network_median_to_contact": round(median(all_contact), 1) if all_contact else None}


def credentialing_impact(ds, an) -> dict:
    """If this doctor were credentialed at that hospital, which of today's problems would have been covered?"""
    world = an.world
    names = {h.id: h.name for h in ds.ref.hospitals.values()}
    creds = ds.ref.credentials
    credit: dict[tuple[str, str], list[str]] = {}

    def add(pid, hid, what):
        credit.setdefault((pid, hid), []).append(what)

    for ev in ds.events:
        if ev.event_type != "routing_attempt_failed" or ev.event_time > world.now or ev.ingested_at > world.now:
            continue
        w = replay(ds, ev.event_time - timedelta(seconds=1))
        for p in w.physicians.values():
            if p.on_shift and ev.hospital_id not in creds.get(p.id, set()) and routing_blocker(p, w, ds.cfg) is None:
                add(p.id, ev.hospital_id, f"failed routing for {ev.consult_id} at {fmt_clock(ev.event_time)}")
    for g in an.now_gaps:
        for p in world.physicians.values():
            if p.on_shift and g.hospital_id not in creds.get(p.id, set()) and routing_blocker(p, world, ds.cfg) is None:
                add(p.id, g.hospital_id, f"nobody free now ({', '.join(g.consult_ids)})")
    for g in an.gaps:
        if g.shortfall != 1:
            continue
        for p in world.physicians.values():
            w = p.window_at(g.start)
            if w and g.hospital_id not in creds.get(p.id, set()) and p.self_status != "Temporarily Unavailable":
                add(p.id, g.hospital_id, f"the {fmt_clock(g.start)} coverage gap")
    rows = []
    for (pid, hid), whats in credit.items():
        p = world.physicians[pid]
        rows.append({"physician_id": pid, "name": p.name, "hospital_id": hid, "hospital": names.get(hid, hid),
                     "covered": len(whats), "what": whats,
                     "text": f"Credentialing {p.name} at {names.get(hid, hid)} would have covered "
                             f"{len(whats)} problem{'s' if len(whats) != 1 else ''} today: {'; '.join(whats)}."})
    rows.sort(key=lambda r: (-r["covered"], r["name"], r["hospital"]))
    workload = []
    on = [p for p in world.physicians.values() if p.on_shift]
    for p in sorted(on, key=lambda p: p.consults_this_shift):
        workload.append({"physician_id": p.id, "name": p.name, "credentialed_at": len(creds.get(p.id, set())),
                         "consults_this_shift": p.consults_this_shift})
    note = ""
    if workload:
        low = workload[0]
        note = (f"{low['name']} has {low['consults_this_shift']} consults this shift and is credentialed at "
                f"{low['credentialed_at']} hospitals: uneven workload caused by credentialing, not effort.")
    return {"top": rows[:10], "workload": workload, "note": note}


WHATS_LEFT = {
    "Assigned": "The hospital has not reached the doctor yet. Make sure someone takes the call.",
    "Provider Contacted": "Assessment in progress on the phone.",
    "CT/CTA or Imaging Pending": "Waiting on imaging from the hospital, then review and video.",
    "Imaging Review": "Review imaging and start the video.",
    "Video Consult In Progress": "Finish the video, then write the note.",
    "Note Pending": "Write and sign the note.",
    "Documentation": "Finish and sign the note.",
}


def handoffs(ds, an) -> list[dict]:
    """I-PASS style cards for every open consult of a doctor within 60 minutes of shift end. Operational data only."""
    world = an.world
    window = float(ds.cfg.get_path("standards.open_notes_near_shift_end.window_minutes", 60))
    names = {h.id: h.name for h in ds.ref.hospitals.values()}
    cards = []
    for p in sorted(world.physicians.values(), key=lambda p: (p.minutes_to_shift_end or 9999)):
        if not p.on_shift or p.minutes_to_shift_end is None or p.minutes_to_shift_end > window:
            continue
        end = p.shift.end
        for cid in p.open_consult_ids:
            c = world.consults[cid]
            chk = an.standards.get(cid)
            receivers = []
            for q in world.physicians.values():
                if q.id == p.id or c.hospital_id not in ds.ref.credentials.get(q.id, set()):
                    continue
                w = q.window_at(end + timedelta(minutes=1))
                if w and q.self_status != "Temporarily Unavailable":
                    receivers.append(q)
            receivers.sort(key=lambda q: q.consults_this_shift)
            notes_stage = c.stage in ("Note Pending", "Documentation")
            watch = "Within its time standard."
            if chk and chk.status in ("warning", "critical"):
                watch = f"{chk.name}: {chk.status} at {chk.minutes} min."
            elif chk and chk.minutes_to_warning is not None:
                watch = f"{chk.name}: warning in {chk.minutes_to_warning} min."
            cards.append({
                "physician_id": p.id, "physician": p.name, "shift_end": end.isoformat(),
                "minutes_to_shift_end": p.minutes_to_shift_end, "consult_id": cid,
                "hospital": names.get(c.hospital_id, c.hospital_id), "severity": c.consult_type,
                "stage": c.stage, "minutes_in_stage": minutes_between(c.stage_entered_at, world.now),
                "whats_left": WHATS_LEFT.get(c.stage, "Continue the consult."), "watch": watch,
                "suggestion": (f"Best finished by {p.name} before {fmt_clock(end)}." if notes_stage else
                               f"Hand off to {receivers[0].name}." if receivers else
                               "Nobody credentialed here is on shift after this doctor leaves. Escalate."),
                "receiver_id": receivers[0].id if receivers and not notes_stage else None,
                "receiver_options": [q.name for q in receivers[:3]],
            })
    return cards
