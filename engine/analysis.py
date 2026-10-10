"""Runs detect and decide over a replayed world: standards, surge, coverage, routing, issues, plans."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .coverage import Gap, PlannedJoin, coverage_point, project, staffed_at
from .demand import SurgeStatus, surge_at, surge_persists_until
from .eligibility import EligibilityResult, evaluate
from .forecast import FreeIn, free_in, team_medians
from .interventions import Context, Option, Plan
from .issues import Issue, IssueGroup, detect, group_issues
from .routing import RouteResult, next_up_all, routing_blocker
from .standards import OpenNotesCheck, StandardCheck, check_consult, check_open_notes
from .timeline import World
from .timeutil import fmt_clock, join_names


@dataclass
class Analysis:
    ds: object
    world: World
    surges: dict[str, SurgeStatus] = field(default_factory=dict)
    surge_until: dict[str, datetime | None] = field(default_factory=dict)
    next_up: dict[str, RouteResult] = field(default_factory=dict)
    team: dict = field(default_factory=dict)
    free_in: dict[str, FreeIn] = field(default_factory=dict)
    standards: dict[str, StandardCheck] = field(default_factory=dict)
    open_notes: dict[str, OpenNotesCheck] = field(default_factory=dict)
    coverage_now: dict[str, dict] = field(default_factory=dict)
    projection: dict = field(default_factory=dict)
    gaps: list[Gap] = field(default_factory=list)
    now_gaps: list[Gap] = field(default_factory=list)
    cluster: EligibilityResult | None = None
    gap_eligibility: dict[str, EligibilityResult] = field(default_factory=dict)
    gap_plans: dict[str, Plan] = field(default_factory=dict)
    next_change: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    groups: list[IssueGroup] = field(default_factory=list)
    ctx: Context | None = None

    @property
    def now_gap_hospitals(self) -> set[str]:
        return {g.hospital_id for g in self.now_gaps}

    @property
    def all_gaps(self) -> list[Gap]:
        return self.now_gaps + self.gaps


def _waiting_consults(world: World, hospital_id: str) -> list[str]:
    """Consults at this hospital that need a doctor and are not already ringing to one."""
    out = []
    for c in world.consults.values():
        if not c.is_open or c.hospital_id != hospital_id:
            continue
        p = world.physicians.get(c.physician_id or "")
        if not c.physician_id and c.stage in ("Received", "Triaged") and not c.ringing_to:
            out.append(c.consult_id)
        elif c.stage == "Assigned" and p is not None and p.verdict in ("unavailable", "off_shift"):
            out.append(c.consult_id)
    return sorted(out)


def analyze(ds, world: World, joins: list[PlannedJoin] = (), history: dict | None = None) -> Analysis:
    cfg = ds.cfg
    now = world.now
    an = Analysis(ds, world)
    names = {h.id: h.name for h in ds.ref.hospitals.values()}

    for hid in ds.ref.hospitals:
        s = surge_at(ds.ref, world.consults, hid, now, cfg)
        an.surges[hid] = s
        an.surge_until[hid] = surge_persists_until(s, cfg)
    an.next_up = next_up_all(ds, world)
    an.team = team_medians(world)
    an.free_in = {pid: free_in(p, world, an.team) for pid, p in world.physicians.items()}
    an.standards = {cid: chk for cid, c in world.consults.items() if c.is_open
                    for chk in [check_consult(c, now, cfg)] if chk}
    an.open_notes = {pid: chk for pid, p in world.physicians.items()
                     for chk in [check_open_notes(p, world.consults, cfg)] if chk}

    for hid in ds.ref.hospitals:
        cp = coverage_point(ds, world, hid, now, joins, an.surge_until)
        free = [pid for pid in cp.staffed if world.physicians[pid].on_shift
                and routing_blocker(world.physicians[pid], world, cfg) is None]
        an.coverage_now[hid] = {"required": cp.required, "base_required": cp.base_required, "surging": cp.surging,
                                "staffed": cp.staffed, "unavailable": cp.unavailable, "free": free,
                                "waiting": _waiting_consults(world, hid)}

    an.projection = project(ds, world, an.surge_until, list(joins))
    an.gaps = an.projection["gaps"]

    now_gap_minutes = float(cfg.get_path("coverage.now_gap_minutes", 60))
    for hid, cov in an.coverage_now.items():
        if cov["waiting"] and not cov["free"]:
            end = now + timedelta(minutes=now_gap_minutes)
            until = an.surge_until.get(hid)
            if until and until > end:
                end = until
            hname = names.get(hid, hid)
            gap = Gap(f"now-{hid}", hid, "now", now, end, cov["required"], 0, max(1, len(cov["waiting"])),
                      ["no_one_free"], [f"Nobody credentialed at {hname} is free, and "
                                        f"{', '.join(cov['waiting'])} {'is' if len(cov['waiting']) == 1 else 'are'} waiting."],
                      consult_ids=cov["waiting"])
            if cov["surging"]:
                gap.causes.append("surge")
            an.now_gaps.append(gap)

    if an.now_gaps:
        an.cluster = evaluate(ds, world, an.now_gaps, an.all_gaps, history)
    an.ctx = Context(ds, world, an.free_in, an.cluster, list(joins))

    for hid in ds.ref.hospitals:
        an.next_change[hid] = _next_change_text(an, hid)

    cluster_best = an.cluster.best if an.cluster else None
    for gap in an.gaps:
        result = evaluate(ds, world, [gap], an.all_gaps, history)
        an.gap_eligibility[gap.id] = result
        an.gap_plans[gap.id] = _gap_plan(an, gap, result, cluster_best)

    an.issues = detect(an)
    an.groups = group_issues(an, an.issues)
    return an


def _gap_plan(an: Analysis, gap: Gap, result: EligibilityResult, cluster_best) -> Plan:
    names = {h.id: h.name for h in an.ds.ref.hospitals.values()}
    if cluster_best and cluster_best.offer and gap.id in cluster_best.offer.gap_ids:
        o = cluster_best.offer
        opt = Option("covered_by_offer", f"Covered by the same offer to {cluster_best.name} "
                                         f"({fmt_clock(o.start)} to {fmt_clock(o.end)} ET)", True, "clinical_lead",
                     cluster_best.physician_id, None,
                     why=(f"One offer covers {', '.join(names[h] for h in o.hospitals)}. Approving it also closes "
                          f"this gap."))
        return Plan(opt, [], [], [])
    best = result.best
    if best:
        o = best.offer
        opt = Option("split_shift", f"Offer {best.name} a split shift: {', '.join(names[h] for h in o.hospitals)}, "
                                    f"{fmt_clock(o.start)} to {fmt_clock(o.end)} ET", True, "clinical_lead",
                     best.physician_id, None,
                     why=(f"{best.name} is the {'only' if len(result.eligible) == 1 else 'top-ranked'} eligible "
                          f"off-duty doctor for this gap."),
                     detail={"offer": o.to_dict(names), "eligible": [r.physician_id for r in result.eligible]})
        return Plan(opt, [], [], [])
    fails = "; ".join(f"{r.name}: {r.first_failure}" for r in result.rows)
    opt = Option("escalate_lead", f"Escalate the {names.get(gap.hospital_id)} gap to the clinical lead", True,
                 "clinical_lead", None, None,
                 why="No off-duty doctor meets every split-shift rule, so this needs a decision from the clinical lead. "
                     "No rule is bent to make a match.")
    split = Option("split_shift", "Offer a split shift", False, "clinical_lead", why_not=fails)
    return Plan(opt, [], [], [split])


def _next_change_text(an: Analysis, hid: str) -> str:
    """One sentence on how staffing at this hospital changes within the projection window."""
    world = an.world
    gaps = [g for g in an.gaps if g.hospital_id == hid]
    if gaps:
        g = gaps[0]
        staffed, _ = staffed_at(an.ds, world, hid, g.start)
        who = (" (" + ", ".join(world.physicians[p].name for p in staffed) + ")") if staffed else ""
        return (f"From {fmt_clock(g.start)}, {g.staffed} credentialed doctor{'s' if g.staffed != 1 else ''}{who} "
                f"against {g.required} required.")
    now_staffed = set(an.coverage_now[hid]["staffed"])
    for point in an.projection["hospitals"][hid]:
        t = datetime.fromisoformat(point["t"])
        ids = set(point["staffed_ids"])
        left = now_staffed - ids
        if left and t > world.now:
            names = join_names([world.physicians[p].name for p in sorted(left)])
            remain = f"{len(ids)} credentialed doctor{'s remain' if len(ids) != 1 else ' remains'}"
            return f"By {fmt_clock(t)}, {names} will have left and {remain}."
    return ""
