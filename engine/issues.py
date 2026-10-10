"""Issue detection, ranking, and grouping. Each issue answers four questions:
what is going on, where, why it matters, and what to do (with who approves and why this fix).

Ranking is fixed and explainable: stroke before general neurology; critical before warning before
projected; nobody working on it before a delayed doctor before a hospital-side delay; re-calls move
up; longer wait before shorter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .interventions import Context, Need, Option, Plan, hospital_escalation, plan_for
from .timeutil import fmt_clock, minutes_between

SEVERITY_RANK = {"critical": 0, "warning": 1, "projected": 2, "info": 3}
CATEGORY = {
    "data_stale": 0, "unassigned_routing_failed": 0, "assigned_unavailable": 0,
    "standard_breach": 1, "recall": 1, "missed_call": 1,
    "hospital_delay": 2, "hospital_tech": 2,
    "open_notes_shift_end": 3, "surge": 3, "note_overdue": 3.5,
    "coverage_gap_projected": 4,
    "signal_conflict": 5, "signal_unverified": 5, "triage_delay": 5,
}


@dataclass
class Issue:
    id: str
    type: str
    severity: str
    stroke: bool
    title: str
    what: str
    where: dict
    why: str
    plan: Plan | None
    group_key: str
    wait_minutes: int = 0
    recall_count: int = 0
    rank: int = 0

    @property
    def sort_key(self) -> tuple:
        return (0 if self.type == "data_stale" else 1, 0 if self.stroke else 1, SEVERITY_RANK[self.severity],
                CATEGORY.get(self.type, 9), -self.recall_count, -self.wait_minutes, self.id)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "severity": self.severity, "stroke": self.stroke, "rank": self.rank,
                "title": self.title, "what": self.what, "where": self.where, "why": self.why,
                "what_to_do": self.plan.to_dict() if self.plan else None, "wait_minutes": self.wait_minutes,
                "recall_count": self.recall_count, "group_key": self.group_key}


@dataclass
class IssueGroup:
    key: str
    title: str
    issues: list[Issue] = field(default_factory=list)
    rank: int = 0

    @property
    def top(self) -> Issue:
        return min(self.issues, key=lambda i: i.sort_key)

    def to_dict(self) -> dict:
        top = self.top
        hospital_ids = sorted({i.where.get("hospital_id") for i in self.issues if i.where.get("hospital_id")})
        return {"id": f"grp-{self.key}", "rank": self.rank, "severity": top.severity, "stroke": top.stroke,
                "title": self.title, "hospital_ids": hospital_ids, "primary_issue_id": top.id,
                "issues": [i.to_dict() for i in sorted(self.issues, key=lambda i: i.sort_key)]}


def _where(an, hospital_id=None, consult_id=None, physician_id=None) -> dict:
    h = an.ds.ref.hospitals.get(hospital_id or "")
    p = an.world.physicians.get(physician_id or "")
    return {"hospital_id": hospital_id, "hospital_name": h.name if h else None, "consult_id": consult_id,
            "physician_id": physician_id, "physician_name": p.name if p else None}


def _kind(c) -> str:
    return "stroke" if c.is_stroke else "general neurology"


def _later(an, hospital_id: str) -> str:
    """'Gets worse' sentence for a hospital: the next staffing change in the projection."""
    change = an.next_change.get(hospital_id)
    if not change:
        return ""
    return change


def detect(an) -> list[Issue]:
    ds, world, now = an.ds, an.world, an.world.now
    issues: list[Issue] = []
    ctx = an.ctx
    hosp = lambda hid: an.ds.ref.hospitals[hid].name if hid in an.ds.ref.hospitals else hid  # noqa: E731

    # Data freshness first: if the feed is quiet, everything else may be out of date.
    stale_after = float(ds.cfg.get_path("alerts.stale_data_minutes", 10))
    if world.last_ingested_at and minutes_between(world.last_ingested_at, now) >= stale_after:
        age = minutes_between(world.last_ingested_at, now)
        issues.append(Issue("iss-data-stale", "data_stale", "warning", False, "No new records",
                            f"No new records have arrived for {age} minutes.", {},
                            f"Everything on this screen reflects {fmt_clock(world.last_ingested_at)}. "
                            "Busy doctors may have freed up, or new consults may be waiting.",
                            Plan(Option("check_feed", "Check the Synapse event feed", True, "coordinator", why=
                                        "Confirm the feed is running before acting on stale information."), [], [], []),
                            "data", wait_minutes=age))

    consult_issue_ids = set()
    ordered = sorted(world.consults.values(), key=lambda c: (not c.is_stroke, c.received_at))
    for c in ordered:
        if not c.is_open:
            continue
        check = an.standards.get(c.consult_id)
        waited = minutes_between(c.received_at, now)
        recall_txt = (f" The hospital has called again {c.recall_count} time{'s' if c.recall_count > 1 else ''} "
                      f"(last at {fmt_clock(c.last_recall_at)}); same patient, clock not restarted."
                      if c.recall_count else "")
        p = world.physicians.get(c.physician_id or "")
        hid = c.hospital_id
        status = check.status if check else "none"
        severity = status if status in ("critical", "warning") else None
        limit_txt = (f"{check.minutes} min in this step against a {int(check.critical)}-minute critical limit"
                     if check and check.critical is not None else "")
        if c.recall_count and severity == "warning":
            severity = "critical"
        need_critical = severity == "critical"

        nobody_free = not an.coverage_now.get(hid, {}).get("free") and not c.ringing_to
        if c.stage in ("Received", "Triaged") and not c.physician_id and (c.routing_failures or severity or nobody_free):
            failures = len(c.routing_failures)
            sev = severity or ("warning" if (c.is_stroke or failures) else "info")
            next_up = an.next_up.get(hid)
            plan = _defer(ctx, Need(hid, c, c.is_stroke, sev == "critical"))
            why = (f"Waiting {waited} min since the request. {limit_txt + '. ' if limit_txt else ''}"
                   + (f"Routing has failed {failures} time{'s' if failures != 1 else ''} (last at "
                      f"{fmt_clock(c.routing_failures[-1])}). " if failures else
                      "No routing attempt has failed yet, but nobody credentialed here is free. ") +
                   f"{next_up.reason if next_up and not next_up.chosen else ''}{recall_txt} {_later(an, hid)} "
                   f"{_soonest_free(an, hid)}").strip()
            issues.append(Issue(f"iss-unassigned-{c.consult_id}", "unassigned_routing_failed", sev, c.is_stroke,
                                f"{c.consult_type} consult waiting with nobody to take it",
                                f"A {_kind(c)} consult at {hosp(hid)} is waiting and nobody approved there can take it.",
                                _where(an, hid, c.consult_id), why, plan, hid, waited, c.recall_count))
            consult_issue_ids.add(c.consult_id)
            continue

        if c.stage == "Assigned" and p is not None and p.verdict in ("unavailable", "off_shift"):
            sev = severity or "warning"
            plan = _defer(ctx, Need(hid, c, c.is_stroke, sev == "critical"))
            why = (f"Assigned {minutes_between(c.assigned_at, now)} min ago and the hospital has not reached the doctor. "
                   f"{limit_txt + '. ' if limit_txt else ''}{p.name}: {p.verdict_reason} "
                   f"Routing treats this consult as handled, so nobody else will pick it up.{recall_txt} "
                   f"{_later(an, hid)}").strip()
            issues.append(Issue(f"iss-assigned-unavailable-{c.consult_id}", "assigned_unavailable", sev, c.is_stroke,
                                f"{c.consult_type} consult assigned to an unavailable doctor",
                                f"A {_kind(c)} consult at {hosp(hid)} is assigned to {p.name}, who is not available.",
                                _where(an, hid, c.consult_id, p.id), why, plan, hid, waited, c.recall_count))
            consult_issue_ids.add(c.consult_id)
            continue

        if not severity:
            if c.recall_count:
                plan = _defer(ctx, Need(hid, c, c.is_stroke, False))
                issues.append(Issue(f"iss-recall-{c.consult_id}", "recall", "warning", c.is_stroke,
                                    "Hospital called again", f"{hosp(hid)} called again about {c.consult_id}.",
                                    _where(an, hid, c.consult_id, c.physician_id),
                                    f"Waiting {waited} min since the first call.{recall_txt}", plan, hid, waited,
                                    c.recall_count))
            elif check and check.status == "info":
                issues.append(Issue(f"iss-triage-{c.consult_id}", "triage_delay", "info", c.is_stroke,
                                    "Consult not yet triaged",
                                    f"{c.consult_id} at {hosp(hid)} has not been triaged after {check.minutes} min.",
                                    _where(an, hid, c.consult_id), "There is no standard for this step, so it is "
                                    "shown for awareness only.", None, hid, waited))
            continue

        if check.key == "imaging_pending":
            plan = hospital_escalation(ctx, c, "imaging has not come back")
            followups = [t for t, _ in c.call_times if c.imaging_requested_at and t > c.imaging_requested_at]
            chased = (f" {p.name if p else 'The doctor'} already called the hospital about it at "
                      f"{fmt_clock(followups[-1])}." if followups else "")
            waiting = "" if chased or not p else f" {p.name} is assigned and waiting on the hospital."
            why = (f"Imaging requested {check.minutes} min ago against a {int(check.critical)}-minute limit for "
                   f"stroke.{chased}{waiting}{recall_txt}").strip()
            issues.append(Issue(f"iss-hospital-delay-{c.consult_id}", "hospital_delay", severity, c.is_stroke,
                                "Imaging delayed at the hospital",
                                f"A {_kind(c)} consult at {hosp(hid)} is waiting on imaging from the hospital.",
                                _where(an, hid, c.consult_id, c.physician_id), why, plan, hid, waited, c.recall_count))
            continue

        if check.key == "imaging_review":
            if p is not None and p.routable:
                plan = hospital_escalation(ctx, c, "imaging is back and the doctor is free, but video has not started")
                issues.append(Issue(f"iss-hospital-tech-{c.consult_id}", "hospital_tech", severity, c.is_stroke,
                                    "Possible video or technology problem at the hospital",
                                    f"Imaging is back for {c.consult_id} at {hosp(hid)}, {p.name} is free, "
                                    "but the video has not started.",
                                    _where(an, hid, c.consult_id, p.id),
                                    f"{limit_txt}. The doctor is not busy elsewhere, so the hospital side may be stuck.",
                                    plan, hid, waited, c.recall_count))
            else:
                plan = _defer(ctx, Need(hid, c, c.is_stroke, need_critical))
                busy = f"{p.name}: {p.verdict_reason}" if p else "No doctor assigned."
                issues.append(Issue(f"iss-breach-{c.consult_id}", "standard_breach", severity, c.is_stroke,
                                    "Imaging back, video not started",
                                    f"Imaging is back for a {_kind(c)} consult at {hosp(hid)} and the video has not started.",
                                    _where(an, hid, c.consult_id, c.physician_id),
                                    f"{limit_txt}. {busy}{recall_txt} {_later(an, hid)}".strip(), plan, hid, waited,
                                    c.recall_count))
            continue

        if check.key == "assigned_not_contacted":
            plan = _defer(ctx, Need(hid, c, c.is_stroke, need_critical))
            issues.append(Issue(f"iss-breach-{c.consult_id}", "standard_breach", severity, c.is_stroke,
                                "Doctor not yet contacted",
                                f"{c.consult_id} at {hosp(hid)} is assigned to {p.name if p else 'a doctor'} "
                                "but the hospital has not reached them.",
                                _where(an, hid, c.consult_id, c.physician_id), f"{limit_txt}.{recall_txt}", plan, hid,
                                waited, c.recall_count))
            continue

        if check.key in ("note_stroke", "note_general"):
            pid = c.physician_id or "unassigned"
            mins = check.minutes
            leaving = p and p.minutes_to_shift_end is not None and p.minutes_to_shift_end <= 60
            plan = Plan(Option("protect_note_time",
                               f"Steer new calls away from {p.name if p else 'the doctor'} until the note is signed",
                               True, "coordinator", c.physician_id, 0,
                               why=("Their shift ends soon; finishing notes now avoids a handoff." if leaving else
                                    "A short pause in new calls lets the note get signed before its limit.")),
                        [], [], [])
            issues.append(Issue(f"iss-note-{c.consult_id}", "note_overdue", severity, c.is_stroke,
                                f"{c.consult_type} note overdue",
                                f"The note for {c.consult_id} ({_kind(c)}, {hosp(hid)}) is not signed.",
                                _where(an, hid, c.consult_id, c.physician_id),
                                (f"{mins} min since the end of the call or video, against a {int(check.warning)}-minute "
                                 f"warning and {int(check.critical)}-minute critical limit."
                                 + (f" Goes critical in {check.minutes_to_critical} min." if check.minutes_to_critical
                                    is not None else "")),
                                plan, f"phy-{pid}", mins))
            continue

        # Any other breach: physician-side delay.
        plan = _defer(ctx, Need(hid, c, c.is_stroke, need_critical))
        issues.append(Issue(f"iss-breach-{c.consult_id}", "standard_breach", severity, c.is_stroke,
                            f"{check.name}", f"{c.consult_id} at {hosp(hid)} is past its time standard.",
                            _where(an, hid, c.consult_id, c.physician_id), f"{limit_txt}.{recall_txt}", plan, hid,
                            waited, c.recall_count))

    # Open notes close to the end of a shift.
    for pid, chk in an.open_notes.items():
        p = world.physicians[pid]
        if chk.status == "none":
            continue
        handoff = "Hand off or finish before the shift ends; steer new calls to others meanwhile (wind-down mode)."
        plan = Plan(Option("wind_down", f"Wind-down for {p.name}: steer new calls elsewhere and prepare handoff cards",
                           True, "coordinator", pid, 0, why=handoff), [], [], [])
        stroke = any(world.consults[c].is_stroke for c in chk.consult_ids)
        issues.append(Issue(f"iss-open-notes-{pid}", "open_notes_shift_end", chk.status, stroke,
                            f"{len(chk.consult_ids)} open note{'s' if len(chk.consult_ids) != 1 else ''} near shift end",
                            f"{p.name} has {len(chk.consult_ids)} unsigned note{'s' if len(chk.consult_ids) != 1 else ''} "
                            f"and their shift ends in {chk.minutes_to_shift_end} min.",
                            _where(an, None, ", ".join(chk.consult_ids), pid),
                            f"Shift ends at {fmt_clock(p.shift.end)}. Notes: {', '.join(chk.consult_ids)}. "
                            f"Unfinished notes become handoffs, which add risk.", plan, f"phy-{pid}",
                            60 - chk.minutes_to_shift_end))

    # Surges.
    for hid, s in an.surges.items():
        if not s.active:
            continue
        until = an.surge_until.get(hid)
        stroke = any(world.consults[c].is_stroke for c in s.consult_ids if c in world.consults)
        plan = _defer(ctx, Need(hid, None, stroke, False))
        multiple = f" ({s.multiple} times the average for this hour)" if s.multiple else ""
        base = an.coverage_now[hid]["base_required"]
        issues.append(Issue(f"iss-surge-{hid}", "surge", "warning", stroke, "Demand surge",
                            f"{hosp(hid)} has {s.trailing_count} consults in the last hour; its normal busy level "
                            f"(p90) for this hour is {_num(s.p90)}{multiple}.",
                            _where(an, hid), f"Coverage requirement rises from {base} to "
                            f"{base + int(ds.cfg.get_path('demand.surge_extra_required', 1))}, assumed until about "
                            f"{fmt_clock(until)}. Consults: {', '.join(s.consult_ids)}. {_later(an, hid)}".strip(),
                            plan, hid, s.trailing_count))

    # Projected coverage gaps.
    for gap in an.gaps:
        hname = hosp(gap.hospital_id)
        plan = an.gap_plans.get(gap.id)
        when = f"{fmt_clock(gap.start)} to {fmt_clock(gap.end)}" + (" or later" if gap.open_ended else "")
        issues.append(Issue(f"iss-{gap.id}", "coverage_gap_projected", "projected", True,
                            f"Coverage gap from {fmt_clock(gap.start)}",
                            f"{hname} will have {gap.staffed} credentialed doctor{'s' if gap.staffed != 1 else ''} "
                            f"on shift against a requirement of {gap.required}, {when}.",
                            _where(an, gap.hospital_id), " ".join(gap.cause_text), plan, gap.hospital_id,
                            -minutes_between(now, gap.start)))

    # Conflicting or unverified signals.
    for p in world.physicians.values():
        if not p.on_shift:
            continue
        gap_hospitals = {h for h in an.now_gap_hospitals if h in ds.ref.credentials.get(p.id, set())}
        if p.verdict == "probably_busy":
            sev = "warning" if gap_hospitals else "info"
            plan = Plan(Option("verify", f"Ask {p.name} if they are free", True, "coordinator", p.id, 1,
                               why="Signals conflict. A quick check settles it before anyone counts on them."),
                        [], [], [])
            extra = (f" They are credentialed at {', '.join(hosp(h) for h in sorted(gap_hospitals))}, where a consult "
                     "is waiting." if gap_hospitals else "")
            issues.append(Issue(f"iss-signal-{p.id}", "signal_conflict", sev, False, "Signals disagree",
                                f"{p.name}'s camera is on but there is no Synapse video.", _where(an, None, None, p.id),
                                f"{p.verdict_reason} Self-reported status says {p.self_status}.{extra}", plan,
                                f"phy-{p.id}"))
        if "agent_offline" in p.flags:
            ringing = p.ringing
            sev = "warning" if ringing or gap_hospitals else "info"
            mins = minutes_between(p.agent_offline_since, now) if p.agent_offline_since else 0
            ring_txt = (f" A hospital call for {ringing['consult_id']} is ringing to them now, possibly to an empty desk."
                        if ringing else "")
            plan = Plan(Option("verify", f"Confirm {p.name} is at their desk", True, "coordinator", p.id, 1,
                               why="Routing assumes they are available, but nothing confirms it."), [], [], [])
            issues.append(Issue(f"iss-agent-{p.id}", "signal_unverified", sev, False, "Presence not confirmed",
                                f"{p.name}'s desktop agent stopped reporting {mins} min ago.",
                                _where(an, ringing["hospital_id"] if ringing else None,
                                       ringing["consult_id"] if ringing else None, p.id),
                                f"Status says {p.self_status}, but camera state is unknown.{ring_txt}", plan,
                                f"phy-{p.id}", mins))
        for missed in p.missed_rings:
            c = world.consults.get(missed["consult_id"] or "")
            if c and c.is_open and c.stage in ("Received", "Triaged", "Assigned"):
                plan = _defer(ctx, Need(c.hospital_id, c, c.is_stroke, False))
                issues.append(Issue(f"iss-missed-{c.consult_id}", "missed_call", "warning", c.is_stroke,
                                    "Call rang with no answer",
                                    f"The call for {c.consult_id} rang to {p.name} from {fmt_clock(missed['since'])} "
                                    "with no answer.", _where(an, c.hospital_id, c.consult_id, p.id),
                                    "An unanswered call means the patient is still waiting.", plan, c.hospital_id,
                                    minutes_between(c.received_at, now)))
    return issues


def _defer(ctx: Context, need: Need):
    """Plans are made after ranking, so the most urgent issue gets first claim on scarce doctors."""
    return lambda: plan_for(ctx, need)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text.startswith(("Stroke", "General")) else text


def _soonest_free(an, hospital_id: str) -> str:
    """GPS hint: the credentialed doctor on shift most likely to free up first."""
    best = None
    for pid, fi in an.free_in.items():
        p = an.world.physicians[pid]
        if (not p.on_shift or p.verdict == "unavailable" or fi.minutes is None or fi.minutes == 0
                or hospital_id not in an.ds.ref.credentials.get(pid, set())):
            continue
        if best is None or fi.minutes < best[1].minutes:
            best = (p, fi)
    if not best:
        return ""
    p, fi = best
    return (f"Watch: {p.name} may be free in about {fi.minutes} min, based on {fi.basis}. "
            f"That is an estimate, not a plan.")


def _num(x) -> str:
    return f"{x:g}" if x is not None else "unknown"


def group_issues(an, issues: list[Issue]) -> list[IssueGroup]:
    groups: dict[str, IssueGroup] = {}
    for issue in issues:
        g = groups.setdefault(issue.group_key, IssueGroup(issue.group_key, ""))
        g.issues.append(issue)
    for issue in sorted(issues, key=lambda i: i.sort_key):
        if callable(issue.plan):
            issue.plan = issue.plan()
    ordered = sorted(groups.values(), key=lambda g: g.top.sort_key)
    for rank, g in enumerate(ordered, start=1):
        g.rank = rank
        top = g.top
        if g.key.startswith("phy-"):
            name = an.world.physicians[g.key[4:]].name if g.key[4:] in an.world.physicians else g.key[4:]
            g.title = f"{name}: {_lower_first(top.title)}"
        elif g.key == "data":
            g.title = top.title
        else:
            hname = an.ds.ref.hospitals[g.key].name if g.key in an.ds.ref.hospitals else g.key
            g.title = f"{hname}: {_lower_first(top.title)}"
    rank = 1
    for g in ordered:
        for i in sorted(g.issues, key=lambda i: i.sort_key):
            i.rank = rank
            rank += 1
    return ordered
