"""Intervention ladder, least disruptive first. Every step checks credentialing.

1. Reassign to a free credentialed on-shift physician (routing rules).
2. Wait for the assigned physician when they are expected to free up soon (the "GPS" estimate).
3. Pull a credentialed on-shift physician off lower-priority work (never a stroke call or video).
4. Ask a scheduled physician to start early (within an hour of their shift).
5. Offer a split shift to an eligible off-duty physician (clinical lead approves).
6. Escalate to the clinical lead. Nothing works? Escalate. Never bend a rule.
Hospital-side delays escalate to the hospital instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .consults import ConsultState
from .eligibility import EligibilityResult
from .routing import candidates_from_world, route_core
from .timeutil import fmt_clock, fmt_day_clock

APPROVER_LABELS = {"coordinator": "Operations coordinator", "clinical_lead": "Clinical lead on duty", "none": "No approval needed"}


@dataclass
class Option:
    kind: str
    label: str
    viable: bool
    approver: str = "coordinator"
    physician_id: str | None = None
    eta_minutes: int | None = None
    why: str = ""
    why_not: str = ""
    warnings: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "viable": self.viable, "approver": self.approver,
                "approver_label": APPROVER_LABELS.get(self.approver, self.approver),
                "physician_id": self.physician_id, "eta_minutes": self.eta_minutes, "why": self.why,
                "why_not": self.why_not, "warnings": self.warnings, "detail": self.detail}


@dataclass
class Need:
    """A consult (or hospital) that needs a physician now."""
    hospital_id: str
    consult: ConsultState | None
    stroke: bool
    critical: bool


@dataclass
class Plan:
    primary: Option
    also_now: list[Option]
    backups: list[Option]
    alternatives: list[Option]      # considered and ruled out, with why_not

    def to_dict(self) -> dict:
        return {"primary": self.primary.to_dict(), "also_now": [o.to_dict() for o in self.also_now],
                "backups": [o.to_dict() for o in self.backups],
                "alternatives": [o.to_dict() for o in self.alternatives]}


@dataclass
class Context:
    ds: object
    world: object
    free_in: dict                   # physician_id -> FreeIn
    cluster: EligibilityResult | None
    claimed: set[str] = field(default_factory=set)
    joiner_claims: dict[str, str] = field(default_factory=dict)   # physician_id -> consult or hospital it was planned for

    def name(self, pid: str | None) -> str:
        p = self.world.physicians.get(pid or "")
        return p.name if p else (pid or "")

    def hospital(self, hid: str) -> str:
        h = self.ds.ref.hospitals.get(hid)
        return h.name if h else hid

    def credentialed(self, pid: str, hid: str) -> bool:
        return hid in self.ds.ref.credentials.get(pid, set())


def _only_free_doctor_for(ctx: Context, pid: str, skip_hospital: str) -> list[str]:
    """Hospitals where this physician is the only one routing could use right now (protect the last doctor)."""
    cands = candidates_from_world(ctx.ds, ctx.world)
    out = []
    for hid in ctx.ds.ref.hospitals:
        if hid == skip_hospital or not ctx.credentialed(pid, hid):
            continue
        free = [c.id for c in cands if c.on_shift and not c.blocker and hid in c.credentials]
        if free == [pid]:
            out.append(hid)
    return out


def opt_reassign(ctx: Context, need: Need) -> Option:
    exclude = set(ctx.claimed)
    if need.consult and need.consult.physician_id:
        exclude.add(need.consult.physician_id)
    cands = [c for c in candidates_from_world(ctx.ds, ctx.world) if c.id not in exclude]
    result = route_core(cands, need.hospital_id, ctx.ds.cfg, ctx.hospital(need.hospital_id))
    what = need.consult.consult_id if need.consult else "the next consult"
    if not result.chosen:
        removed = [r for step in result.steps if step["rule"] in (1, 2) for r in step["removed"]
                   if ctx.credentialed(r["physician_id"], need.hospital_id)]
        detail = " ".join(f"{ctx.name(r['physician_id'])}: {r['reason']}" for r in removed[:4])
        return Option("reassign", "Reassign to a free doctor on shift", False,
                      why_not=f"Nobody credentialed at {ctx.hospital(need.hospital_id)} is free. {detail}".strip(),
                      detail={"route": result.to_dict()})
    warnings = []
    last_for = _only_free_doctor_for(ctx, result.chosen, need.hospital_id)
    if last_for:
        warnings.append(f"{result.chosen_name} is the only free doctor for "
                        f"{', '.join(ctx.hospital(h) for h in last_for)}. Taking this consult leaves "
                        f"{'it' if len(last_for) == 1 else 'them'} with nobody free.")
    return Option("reassign", f"Reassign {what} to {result.chosen_name}", True, "coordinator", result.chosen, 0,
                  why=result.reason, warnings=warnings, detail={"route": result.to_dict()})


def opt_wait(ctx: Context, need: Need) -> Option:
    c = need.consult
    if not c or not c.physician_id:
        return Option("wait", "Wait for the assigned doctor", False, why_not="No doctor is assigned.")
    p = ctx.world.physicians.get(c.physician_id)
    fi = ctx.free_in.get(c.physician_id)
    if not p or not p.on_shift or p.verdict == "unavailable":
        return Option("wait", f"Wait for {ctx.name(c.physician_id)}", False,
                      why_not=f"{ctx.name(c.physician_id)} is {p.verdict_label.lower() if p else 'unknown'}.")
    if p.routable:
        return Option("wait", f"Contact {p.name}", True, "coordinator", p.id, 0,
                      why=f"{p.name} is free now ({p.verdict_reason}) and already has this consult.")
    if fi is None or fi.minutes is None or fi.minutes > 10:
        reason = fi.basis if fi else "no estimate"
        return Option("wait", f"Wait for {p.name}", False,
                      why_not=f"{p.name} is {p.verdict_label.lower()}; no reliable short estimate ({reason}).")
    return Option("wait", f"Keep {c.consult_id} with {p.name}, who should be free in about {fi.minutes} min", True,
                  "coordinator", p.id, fi.minutes,
                  why=f"{p.verdict_reason} Estimate based on {fi.basis}. Re-check if it runs long.")


def opt_interrupt(ctx: Context, need: Need) -> Option:
    best = None
    blocked = []
    for p in ctx.world.physicians.values():
        if not p.on_shift or p.id in ctx.claimed or not ctx.credentialed(p.id, need.hospital_id):
            continue
        if need.consult and p.id == need.consult.physician_id:
            continue
        a = p.current_activity
        if p.verdict != "busy" or not a:
            continue
        other = ctx.world.consults.get(a.get("consult_id") or "")
        if other is None or other.is_stroke:
            blocked.append(f"{p.name} is on a stroke {a['kind'].replace('_', ' ')} ({a.get('consult_id')}), "
                           "which is never interrupted.")
            continue
        best = (p, a, other)
        break
    if not best:
        return Option("interrupt", "Pull a doctor off lower-priority work", False,
                      why_not=" ".join(blocked) if blocked else
                      "No credentialed doctor on shift is doing lower-priority work that can be interrupted.")
    p, a, other = best
    cost = int(ctx.ds.cfg.get_path("interventions.interrupt_note_cost_minutes", 5))
    return Option("interrupt", f"Ask {p.name} to wrap up the general neurology {a['kind'].replace('_', ' ')} "
                               f"({other.consult_id}) and take this", True, "coordinator", p.id, 5,
                  why="General neurology work can wait; this stroke cannot.",
                  warnings=[f"Adds about {cost} minutes to {other.consult_id}."])


def opt_start_early(ctx: Context, need: Need) -> Option:
    now = ctx.world.now
    max_early = timedelta(minutes=float(ctx.ds.cfg.get_path("interventions.start_early_max_minutes", 60)))
    rest_min = float(ctx.ds.cfg.get_path("split_shift.min_rest_hours", 10))
    max_week = float(ctx.ds.cfg.get_path("split_shift.max_week_hours", 60))
    reasons = []
    for pid, info in sorted(ctx.ds.ref.physicians.items()):
        p = ctx.world.physicians.get(pid)
        if not p or p.on_shift or not ctx.credentialed(pid, need.hospital_id):
            continue
        upcoming = [w for w in p.windows if now < w.start <= now + max_early]
        if not upcoming:
            later = [w for w in p.windows if now + max_early < w.start <= now + timedelta(hours=12)]
            if later:
                reasons.append(f"{info.name} starts at {fmt_day_clock(min(w.start for w in later), now)}, more than "
                               f"{int(max_early.total_seconds() // 60)} min away, so it would be a split shift")
            continue
        if pid in ctx.joiner_claims:
            reasons.append(f"{info.name} is already being asked to start early for {ctx.joiner_claims[pid]}, "
                           "a more urgent consult")
            continue
        w = upcoming[0]
        online = int(info.est_minutes_to_online or 0)
        early_h = (w.start - now).total_seconds() / 3600
        rest = (now - info.last_shift_end).total_seconds() / 3600 if info.last_shift_end else None
        if rest is not None and rest < rest_min:
            reasons.append(f"{info.name} has had only {rest:.1f} hours of rest")
            continue
        week = float(info.hours_worked_last_7d or 0) + early_h
        if week > max_week:
            reasons.append(f"{info.name} would reach {week:.1f} hours this week")
            continue
        return Option("start_early", f"Ask {info.name} to start now instead of {fmt_clock(w.start)}", True,
                      "clinical_lead", pid, online,
                      why=(f"{info.name} is scheduled at {fmt_clock(w.start)}, is credentialed at "
                           f"{ctx.hospital(need.hospital_id)}, can be online in {online} min, and has had "
                           f"{rest:.1f} hours of rest." if rest is not None else
                           f"{info.name} is scheduled at {fmt_clock(w.start)} and credentialed here."),
                      warnings=["Joins with zero consults: the flood guard keeps new calls to the hospitals that need help."],
                      detail={"scheduled_start": w.start.isoformat(), "minutes_early": int(early_h * 60)})
    return Option("start_early", "Ask a scheduled doctor to start early", False,
                  why_not=". ".join(reasons) + "." if reasons else
                  f"No doctor credentialed at {ctx.hospital(need.hospital_id)} starts within the next hour.")


def opt_split_shift(ctx: Context, need: Need) -> Option:
    if ctx.cluster is None:
        return Option("split_shift", "Offer a split shift", False, "clinical_lead", why_not="No gap to offer for.")
    rows = [r for r in ctx.cluster.eligible if r.offer and need.hospital_id in r.offer.hospitals]
    if not rows:
        fails = [f"{r.name}: {r.first_failure}" for r in ctx.cluster.rows if not r.eligible]
        return Option("split_shift", "Offer a split shift to an off-duty doctor", False, "clinical_lead",
                      why_not=(f"No off-duty doctor is eligible for {ctx.hospital(need.hospital_id)}. "
                               + " ".join(fails[:6])))
    best = rows[0]
    o = best.offer
    eta = int((o.online_at - ctx.world.now).total_seconds() // 60)
    hospitals = ", ".join(ctx.hospital(h) for h in o.hospitals)
    others = len(ctx.cluster.rows) - 1
    return Option("split_shift", f"Offer {best.name} a split shift: {hospitals}, {fmt_clock(o.start)} to "
                                 f"{fmt_clock(o.end)} ET", True, "clinical_lead", best.physician_id, eta,
                  why=(f"{best.name} is the {'only' if len(ctx.cluster.eligible) == 1 else 'top-ranked'} eligible "
                       f"off-duty doctor ({others} others checked), credentialed at {hospitals}, online in "
                       f"{o.minutes_to_online} min."),
                  warnings=["Joins with zero consults: the flood guard keeps new calls to the gap hospitals."],
                  detail={"offer": o.to_dict({h.id: h.name for h in ctx.ds.ref.hospitals.values()}),
                          "eligible": [r.physician_id for r in ctx.cluster.eligible]})


def opt_verify(ctx: Context, need: Need) -> list[Option]:
    out = []
    for p in ctx.world.physicians.values():
        if (p.on_shift and p.id not in ctx.claimed and ctx.credentialed(p.id, need.hospital_id)
                and p.verdict == "probably_busy"):
            out.append(Option("verify", f"Ask {p.name} if they are free", True, "coordinator", p.id, 1,
                              why=f"{p.verdict_reason} If free, reassign to them instead."))
    return out


def opt_escalate_lead(ctx: Context, need: Need) -> Option:
    what = f"{need.consult.consult_id} at " if need.consult else ""
    return Option("escalate_lead", f"Escalate {what}{ctx.hospital(need.hospital_id)} to the clinical lead", True,
                  "clinical_lead", None, 0, why="The clinical lead can authorize steps the coordinator cannot.")


def plan_for(ctx: Context, need: Need) -> Plan:
    ladder = [opt_reassign(ctx, need), opt_wait(ctx, need), opt_interrupt(ctx, need), opt_start_early(ctx, need),
              opt_split_shift(ctx, need)]
    viable = [o for o in ladder if o.viable]
    alternatives = [o for o in ladder if not o.viable]
    escalate = opt_escalate_lead(ctx, need)
    also = opt_verify(ctx, need)
    if viable:
        primary, backups = viable[0], viable[1:]
        if need.stroke and need.critical and (primary.eta_minutes or 0) > 0:
            also.append(escalate)
        else:
            backups.append(escalate)
    else:
        primary, backups = escalate, []
        primary.why = "Nothing on the ladder works without bending a rule, so this goes to the clinical lead now."
    if primary.physician_id and primary.kind in ("reassign", "interrupt"):
        ctx.claimed.add(primary.physician_id)
    if primary.kind == "start_early" and primary.physician_id:
        ctx.joiner_claims[primary.physician_id] = need.consult.consult_id if need.consult else need.hospital_id
    return Plan(primary, also, backups, alternatives)


def hospital_escalation(ctx: Context, consult: ConsultState, what: str) -> Plan:
    opt = Option("escalate_hospital", f"Call {ctx.hospital(consult.hospital_id)} about {consult.consult_id}", True,
                 "coordinator", None, 0, why=f"The delay is on the hospital side ({what}). A new doctor would not fix it.")
    staffing = Option("reassign", "Change the doctor", False, why_not="The doctor is not the cause of this delay.")
    return Plan(opt, [], [], [staffing])
