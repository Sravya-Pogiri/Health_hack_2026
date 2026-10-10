"""Split-shift eligibility: every off-shift physician, every rule, pass or fail with the numbers used.

If nobody qualifies, the answer is to escalate. Rules are never bent to make a match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .coverage import Gap
from .timeutil import fmt_clock


@dataclass
class RuleResult:
    key: str
    label: str
    passed: bool
    reason: str
    numbers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "passed": self.passed, "reason": self.reason,
                "numbers": self.numbers}


@dataclass
class OfferWindow:
    start: datetime
    end: datetime
    online_at: datetime
    minutes_to_online: int
    hospitals: list[str]            # hospitals the offer covers (credentialed and in a gap)
    gap_ids: list[str]

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def effective_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60 - self.minutes_to_online

    def to_dict(self, names: dict[str, str]) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "online_at": self.online_at.isoformat(),
                "minutes_to_online": self.minutes_to_online, "hours": round(self.hours, 2),
                "effective_minutes": int(self.effective_minutes),
                "hospitals": [{"id": h, "name": names.get(h, h)} for h in self.hospitals], "gap_ids": self.gap_ids,
                "label": f"{fmt_clock(self.start)} to {fmt_clock(self.end)} ET"}


@dataclass
class EligibilityRow:
    physician_id: str
    name: str
    eligible: bool
    rules: list[RuleResult]
    offer: OfferWindow | None
    first_failure: str | None
    rank: int | None = None
    sort_key: tuple = ()

    def to_dict(self, names: dict[str, str]) -> dict:
        return {"physician_id": self.physician_id, "name": self.name, "eligible": self.eligible, "rank": self.rank,
                "first_failure": self.first_failure, "rules": [r.to_dict() for r in self.rules],
                "offer": self.offer.to_dict(names) if self.offer else None}


@dataclass
class EligibilityResult:
    target_gap_ids: list[str]
    target_hospitals: list[str]
    rows: list[EligibilityRow]

    @property
    def eligible(self) -> list[EligibilityRow]:
        return sorted([r for r in self.rows if r.eligible], key=lambda r: r.rank or 0)

    @property
    def best(self) -> EligibilityRow | None:
        e = self.eligible
        return e[0] if e else None

    def to_dict(self, names: dict[str, str]) -> dict:
        return {"target_gap_ids": self.target_gap_ids,
                "target_hospitals": [{"id": h, "name": names.get(h, h)} for h in self.target_hospitals],
                "eligible_count": len(self.eligible),
                "recommended": self.best.physician_id if self.best else None,
                "rows": [r.to_dict(names) for r in sorted(self.rows, key=lambda r: (not r.eligible, r.rank or 99,
                                                                                    r.physician_id))]}


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600


def _fmt_h(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def build_window(phys, creds: set[str], targets: list[Gap], all_gaps: list[Gap], now: datetime, cfg) -> OfferWindow:
    """Offer start = max(gap start, now + time to online). End covers the gaps, stretched for combined coverage."""
    rules = cfg.get_path("split_shift", {}) or {}
    online = int(phys.est_minutes_to_online or 0)
    mine = [g for g in targets if g.hospital_id in creds] or targets
    start = max(min(g.start for g in mine), now + timedelta(minutes=online))
    end = max(g.end for g in mine)
    covered = {g.id for g in mine if g.hospital_id in creds}
    extend = timedelta(minutes=float(rules.get("extend_for_later_gaps_minutes", 60)))
    changed = True
    while changed:
        changed = False
        for g in all_gaps:
            if g.id in covered or g.hospital_id not in creds:
                continue
            if g.start <= end + extend and g.end > start:
                covered.add(g.id)
                if g.end > end:
                    end = g.end
                changed = True
    min_len = timedelta(minutes=float(rules.get("min_effective_minutes", 120)) + online)
    end = max(end, start + min_len)
    end = min(end, start + timedelta(hours=float(rules.get("max_offer_hours", 4))))
    hospitals = sorted({g.hospital_id for g in all_gaps if g.id in covered} & creds)
    # The start already allows for time to get online when the gap is now, so the doctor is expected online at start.
    return OfferWindow(start, end, start, online, hospitals, sorted(covered))


def evaluate_physician(phys, creds: set[str], targets: list[Gap], all_gaps: list[Gap], now: datetime, cfg,
                       history: dict, names: dict[str, str]) -> EligibilityRow:
    rules = cfg.get_path("split_shift", {}) or {}
    min_rest = float(rules.get("min_rest_hours", 10))
    max_week = float(rules.get("max_week_hours", 60))
    min_eff = float(rules.get("min_effective_minutes", 120))
    max_offers = int(rules.get("max_offers_per_week", 3))
    online = int(phys.est_minutes_to_online or 0)
    target_h = sorted({g.hospital_id for g in targets})
    results: list[RuleResult] = []

    window = build_window(phys, creds, targets, all_gaps, now, cfg)
    # Caps from the weekly limit and the next scheduled shift (the offer may be trimmed, never stretched past them).
    hours7 = float(phys.hours_worked_last_7d or 0)
    week_cap = window.start + timedelta(hours=max(0.0, max_week - hours7))
    if window.end > week_cap:
        window.end = week_cap
    if phys.next_shift_start and phys.next_shift_start > window.start and window.end > phys.next_shift_start:
        window.end = phys.next_shift_start

    opt = phys.opt_in
    results.append(RuleResult("opt_in", "Opted in to split-shift offers", bool(opt),
                              "Opted in." if opt else "Has not opted in to split-shift offers.",
                              {"opt_in": opt}))

    matched = sorted(creds & set(target_h))
    results.append(RuleResult(
        "credentialed", "Credentialed at a gap hospital", bool(matched),
        (f"Credentialed at {', '.join(names.get(h, h) for h in matched)}." if matched else
         f"Not credentialed at any gap hospital ({', '.join(names.get(h, h) for h in target_h)}). "
         f"Credentialed elsewhere does not help."),
        {"gap_hospitals": target_h, "matched": matched}))

    if phys.last_shift_end:
        rest = _hours(window.start - phys.last_shift_end)
        results.append(RuleResult(
            "rest", f"At least {_fmt_h(min_rest)} hours since last shift, at the offered start", rest >= min_rest,
            (f"{_fmt_h(rest)} hours of rest by {fmt_clock(window.start)} (last shift ended "
             f"{phys.last_shift_end.strftime('%b %d')} {fmt_clock(phys.last_shift_end)})."),
            {"rest_hours": round(rest, 2), "minimum": min_rest}))
    else:
        results.append(RuleResult("rest", f"At least {_fmt_h(min_rest)} hours since last shift", False,
                                  "Last shift end is unknown, so rest cannot be confirmed.", {}))

    needed_h = (min_eff + online) / 60
    offered_h = max(window.hours, needed_h)
    total = hours7 + offered_h
    results.append(RuleResult(
        "week_hours", f"Hours in the last 7 days plus the offer at most {_fmt_h(max_week)}", total <= max_week + 1e-9,
        (f"{_fmt_h(hours7)} h worked + {_fmt_h(offered_h)} h offered = {_fmt_h(total)} h"
         + (" (within the limit)." if total <= max_week + 1e-9 else
            f", over {_fmt_h(max_week)}. Even the shortest workable offer ({_fmt_h(needed_h)} h including "
            f"{online} min to get online) is too long.")),
        {"hours_last_7d": hours7, "offered_hours": round(offered_h, 2), "total": round(total, 2), "maximum": max_week}))

    overlap_reason = None
    working = [w for w in phys.scheduled_shifts if w.start <= window.start < w.end]
    if working:
        w = working[0]
        overlap_reason = (f"Already scheduled to work then ({fmt_clock(w.start)} to {fmt_clock(w.end)}), "
                          "so already counted as coverage.")
    elif phys.last_shift_end and window.start < phys.last_shift_end:
        overlap_reason = f"Still on a shift until {fmt_clock(phys.last_shift_end)}."
    elif phys.next_shift_start and phys.next_shift_start < window.start + timedelta(minutes=min_eff + online):
        left = max(0, int((phys.next_shift_start - window.start).total_seconds() // 60) - online)
        overlap_reason = (f"Their scheduled shift starts at {fmt_clock(phys.next_shift_start)}. An offer that ends "
                          f"before it would give {left} minutes, under the {int(min_eff // 60)}-hour minimum.")
    results.append(RuleResult(
        "no_overlap", "Does not overlap a scheduled shift", overlap_reason is None,
        overlap_reason or (f"Next shift starts {phys.next_shift_start.strftime('%b %d')} "
                           f"{fmt_clock(phys.next_shift_start)}, after the offer ends." if phys.next_shift_start
                           else "No next shift on file."),
        {"next_shift_start": phys.next_shift_start.isoformat() if phys.next_shift_start else None}))

    eff = window.effective_minutes
    results.append(RuleResult(
        "value", f"Worth at least {int(min_eff // 60)} hours after time to get online", eff >= min_eff,
        (f"{_fmt_h(window.hours)} h offer minus {online} min to get online = {int(eff)} min of coverage"
         + ("." if eff >= min_eff else f", under {int(min_eff)} minutes.")),
        {"offer_minutes": int(window.hours * 60), "minutes_to_online": online, "effective_minutes": int(eff),
         "minimum": min_eff}))

    week_offers = int(history.get("offers_this_week", 0))
    results.append(RuleResult(
        "weekly_offers", f"Fewer than {max_offers} offers this week", week_offers < max_offers,
        f"{week_offers} offer{'s' if week_offers != 1 else ''} this week.", {"offers_this_week": week_offers,
                                                                            "maximum": max_offers}))
    open_offer = history.get("open_offer")
    results.append(RuleResult(
        "no_open_offer", "No other open offer", not open_offer,
        "No open offer." if not open_offer else f"Already has open offer {open_offer}.", {"open_offer": open_offer}))

    failed = [r for r in results if not r.passed]
    eligible = not failed
    sort_key = (-len(window.hospitals), online, hours7, week_offers, int(phys.offers_declined_last_30d or 0), phys.id)
    return EligibilityRow(phys.id, phys.name, eligible, results, window if eligible else window,
                          failed[0].reason if failed else None, sort_key=sort_key)


def evaluate(ds, world, targets: list[Gap], all_gaps: list[Gap], history: dict[str, dict] | None = None
             ) -> EligibilityResult:
    """Evaluate every off-shift physician in the roster for the target gaps."""
    history = history or {}
    names = {h.id: h.name for h in ds.ref.hospitals.values()}
    rows = []
    for pid, phys in sorted(ds.ref.physicians.items()):
        if not phys.in_roster:
            continue
        state = world.physicians.get(pid)
        if state and state.on_shift:
            continue    # already working; not a split-shift candidate
        creds = set(ds.ref.credentials.get(pid, set()))
        rows.append(evaluate_physician(phys, creds, targets, all_gaps, world.now, ds.cfg, history.get(pid, {}), names))
    for rank, row in enumerate(sorted([r for r in rows if r.eligible], key=lambda r: r.sort_key), start=1):
        row.rank = rank
    return EligibilityResult([g.id for g in targets], sorted({g.hospital_id for g in targets}), rows)
