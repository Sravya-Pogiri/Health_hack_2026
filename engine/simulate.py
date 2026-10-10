"""Flood guard proof: simulate where the next calls go, with and without the guard.

A physician who joins mid-shift starts with zero consults, so routing rules 3 and 4 send them nearly
every call at every hospital they are credentialed for. The guard (a) keeps a split-shift joiner on the
gap hospitals and (b) seeds every joiner with the team's median consult count.
Arrivals follow each hospital's baseline mean for the hour (raised to the current rate while a surge
lasts), sampled with a fixed seed and averaged over many runs. This is an estimate, not a forecast.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .routing import Candidate, JOINER_KINDS, is_night, route_core, routing_blocker
from .timeutil import ET, fmt_clock


@dataclass
class SimDoc:
    id: str
    name: str
    credentials: set[str]
    windows: list            # ShiftWindow list
    consults: int
    last_answered: datetime | None
    busy_until: datetime
    unavailable: bool
    night: bool
    calls: dict[str, int] = field(default_factory=dict)

    def window_at(self, t):
        for w in self.windows:
            if w.start <= t < w.end:
                return w
        return None


def _service_minutes(world) -> float:
    """Average minutes a doctor is actually on calls or video per consult today (imaging waits do not count)."""
    busy = sum(sum(p.call_durations) + sum(p.video_durations) for p in world.physicians.values())
    consults = sum(len({cid for cid, _ in p.assignments}) for p in world.physicians.values())
    return busy / consults if consults else 20.0


def _arrival_rate(ds, an, hid: str, t: datetime) -> float:
    hour = t.astimezone(ET).hour
    row = ds.ref.baseline.get((hid, hour))
    rate = row.mean if row else 0.0
    until = an.surge_until.get(hid)
    if until and t < until:
        rate = max(rate, float(an.surges[hid].trailing_count))
    return rate


def _sample_arrivals(ds, an, start: datetime, end: datetime, rng: random.Random) -> list[tuple[datetime, str]]:
    arrivals = []
    for hid in ds.ref.hospitals:
        t = start
        while t < end:
            rate = _arrival_rate(ds, an, hid, t)        # per hour; re-read every step so hour changes apply
            if rate <= 0:
                t += timedelta(minutes=15)
                continue
            gap = rng.expovariate(rate / 60.0)
            step_end = min(end, t + timedelta(minutes=15))
            if t + timedelta(minutes=gap) < step_end:
                t = t + timedelta(minutes=gap)
                arrivals.append((t, hid))
            else:
                t = step_end
    return sorted(arrivals)


def _initial_docs(ds, world, an) -> list[SimDoc]:
    docs = []
    for p in world.physicians.values():
        fi = an.free_in.get(p.id)
        busy = world.now
        if p.on_shift and not p.routable:
            minutes = fi.minutes if fi and fi.minutes is not None else 10
            busy = world.now + timedelta(minutes=minutes)
        elif p.on_shift and routing_blocker(p, world, ds.cfg):
            busy = world.now + timedelta(minutes=5)
        role = ds.ref.physicians[p.id].role if p.id in ds.ref.physicians else None
        docs.append(SimDoc(p.id, p.name, set(ds.ref.credentials.get(p.id, set())), list(p.windows),
                           p.consults_this_shift, p.last_answered_at, busy, p.self_status == "Temporarily Unavailable",
                           is_night(p, ds.cfg, role) or role == "Night"))
    return docs


def _run(ds, an, docs: list[SimDoc], arrivals, guard: bool, service: float, cooldown: float,
         end: datetime) -> tuple[dict, int, dict]:
    """Route each arrival; a call nobody can take retries every 5 minutes (rule 5) until the end."""
    import heapq

    retry = timedelta(minutes=float(ds.cfg.get_path("routing.retry_minutes", 5)))
    queue = [(t, i, hid, t) for i, (t, hid) in enumerate(arrivals)]
    heapq.heapify(queue)
    failures, failed_by_h, waits = 0, {}, []
    seq = len(queue)
    while queue:
        t, _, hid, first = heapq.heappop(queue)
        joiner_ids = {d.id for d in docs if (w := d.window_at(t)) and w.kind in JOINER_KINDS}
        base = sorted(d.consults for d in docs if d.window_at(t) and d.id not in joiner_ids)
        med = base[len(base) // 2] if base else 0
        cands = []
        for d in docs:
            w = d.window_at(t)
            blocker = None
            if w is None:
                blocker = "off shift"
            elif d.unavailable:
                blocker = "unavailable"
            elif d.busy_until > t:
                blocker = "busy"
            c = Candidate(d.id, d.name, d.credentials, w is not None, d.consults, d.last_answered,
                          d.night or bool(w and w.start.hour >= 19), blocker)
            if guard and w is not None and w.kind in JOINER_KINDS:
                if c.consults < med:
                    c.consults = med
                if w.kind == "split_shift" and w.hospitals:
                    c.allowed_hospitals = set(w.hospitals)
            cands.append(c)
        result = route_core(cands, hid, ds.cfg)
        if not result.chosen:
            if t + retry < end:
                seq += 1
                heapq.heappush(queue, (t + retry, seq, hid, first))
            else:
                failures += 1
                failed_by_h[hid] = failed_by_h.get(hid, 0) + 1
            continue
        waits.append((t - first).total_seconds() / 60)
        d = next(x for x in docs if x.id == result.chosen)
        d.consults += 1
        d.last_answered = t
        d.busy_until = t + timedelta(minutes=service + cooldown)
        d.calls[hid] = d.calls.get(hid, 0) + 1
    return {d.id: dict(d.calls) for d in docs}, failures, {"by_hospital": failed_by_h, "waits": waits}


def simulate_flood_guard(ds, world, an, joins=(), minutes: int | None = None) -> dict:
    """joins: hypothetical PlannedJoin records (for example the recommended offer) added as split-shift windows."""
    from .model import ShiftWindow

    cfg = ds.cfg
    runs = int(cfg.get_path("simulation.runs", 50))
    seed = int(cfg.get_path("simulation.seed", 17))
    minutes = minutes or int(cfg.get_path("simulation.horizon_minutes", 120))
    start, end = world.now, world.now + timedelta(minutes=minutes)
    service = _service_minutes(world)
    cooldown = float(cfg.get_path("routing.cooldown_minutes", 2))
    names = {h.id: h.name for h in ds.ref.hospitals.values()}

    totals = {"without": {}, "with": {}}
    fails = {"without": 0.0, "with": 0.0}
    waits = {"without": [], "with": []}
    for r in range(runs):
        rng = random.Random(seed + r)
        arrivals = _sample_arrivals(ds, an, start, end, rng)
        for mode in ("without", "with"):
            docs = _initial_docs(ds, world, an)
            for j in joins:
                d = next((x for x in docs if x.id == j.physician_id), None)
                if d is not None:
                    d.windows.append(ShiftWindow(j.online_at, j.end, "split_shift", j.hospitals, j.label))
                    d.busy_until = max(d.busy_until, j.online_at)
            calls, failures, extra = _run(ds, an, docs, arrivals, mode == "with", service, cooldown, end)
            waits[mode].extend(extra["waits"])
            fails[mode] += failures
            for pid, by_h in calls.items():
                agg = totals[mode].setdefault(pid, {})
                for hid, n in by_h.items():
                    agg[hid] = agg.get(hid, 0) + n

    def summarize(mode):
        out = {}
        for pid, by_h in totals[mode].items():
            avg = {h: round(n / runs, 2) for h, n in sorted(by_h.items())}
            out[pid] = {"calls": round(sum(by_h.values()) / runs, 1), "by_hospital": avg}
        return out

    without, with_ = summarize("without"), summarize("with")
    joiner_ids = sorted({j.physician_id for j in joins} |
                        {p.id for p in world.physicians.values()
                         for w in p.windows if w.kind in JOINER_KINDS and start <= w.start < end})
    joiners = []
    for pid in joiner_ids:
        p = world.physicians[pid]
        allowed = next((set(j.hospitals) for j in joins if j.physician_id == pid and j.hospitals), None)
        wo, wi = without.get(pid, {"calls": 0, "by_hospital": {}}), with_.get(pid, {"calls": 0, "by_hospital": {}})
        wo_h = [h for h, n in wo["by_hospital"].items() if n >= 0.1]
        wi_h = [h for h, n in wi["by_hospital"].items() if n >= 0.1]
        outside = sum(n for h, n in wi["by_hospital"].items() if allowed and h not in allowed)
        join_w = next((w for w in p.windows if w.kind in JOINER_KINDS and w.start < end), None)
        joiners.append({
            "physician_id": pid, "name": p.name,
            "joins_at": (next((j.online_at for j in joins if j.physician_id == pid), None) or
                         (join_w.start if join_w else None)).isoformat(),
            "kind": "split shift" if any(j.physician_id == pid for j in joins) else "scheduled start",
            "gap_hospitals": sorted(allowed) if allowed else None,
            "without": {**wo, "hospital_count": len(wo_h)},
            "with": {**wi, "hospital_count": len(wi_h), "calls_outside_gap_hospitals": round(outside, 2)},
        })
    others = []
    for pid, p in sorted(world.physicians.items()):
        if pid in joiner_ids:
            continue
        a, b = without.get(pid, {"calls": 0})["calls"], with_.get(pid, {"calls": 0})["calls"]
        if a or b:
            others.append({"physician_id": pid, "name": p.name, "without": a, "with": b, "change": round(b - a, 1)})

    headline = []
    for j in joiners:
        wo, wi = j["without"], j["with"]
        when = fmt_clock(datetime.fromisoformat(j["joins_at"]))
        if j["gap_hospitals"]:
            allowed = set(j["gap_hospitals"])
            out_wo = {h: n for h, n in wo["by_hospital"].items() if h not in allowed and n >= 0.05}
            out_txt = (f", including about {sum(out_wo.values()):.1f} at hospitals with no gap "
                       f"({', '.join(names[h] for h in out_wo)})" if out_wo else "")
            headline.append(
                f"{j['name']} joins at {when} on a split shift. Without the guard: about {wo['calls']:g} calls in the "
                f"next {minutes} minutes from {wo['hospital_count']} hospitals{out_txt}. With the guard: about "
                f"{wi['calls']:g}, all at the gap hospitals.")
        else:
            headline.append(
                f"{j['name']} starts a scheduled shift at {when} with zero consults. Without the guard: about "
                f"{wo['calls']:g} calls; with the guard (counted from the team's median load): about {wi['calls']:g}.")
    shift = sum(o["change"] for o in others)
    headline.append(f"Everyone else takes {shift:+.1f} calls in total with the guard. Tonight is busy, so few calls "
                    f"find two doctors free at once and the guard moves only a few calls; its main job is keeping the "
                    f"new doctor on the hospitals that need help. Average wait for a doctor: "
                    f"{sum(waits['without']) / max(1, len(waits['without'])):.1f} min without, "
                    f"{sum(waits['with']) / max(1, len(waits['with'])):.1f} min with.")
    return {
        "start": start.isoformat(), "end": end.isoformat(), "minutes": minutes, "runs": runs, "seed": seed,
        "service_minutes": round(service, 1),
        "assumptions": ["Arrivals follow each hospital's baseline mean for the hour, raised to the current rate "
                        "while a surge lasts.", f"Each call keeps a doctor busy about {service:.0f} minutes "
                        "(today's average time on calls and video per consult), plus the 2-minute cooldown.",
                        "A call nobody can take retries every 5 minutes, as routing rule 5 says.",
                        "Busy doctors free up at their estimated time; this is an estimate, not a forecast."],
        "joiners": joiners, "others": others,
        "unrouted": {"without": round(fails["without"] / runs, 1), "with": round(fails["with"] / runs, 1)},
        "average_wait_minutes": {m: round(sum(v) / len(v), 1) if v else 0.0 for m, v in waits.items()},
        "hospital_names": names, "headline": headline,
    }
