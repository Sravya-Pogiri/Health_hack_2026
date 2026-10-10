"""The "GPS" view: when will a busy physician likely be free, based on today's typical durations."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .timeline import World


@dataclass
class FreeIn:
    minutes: int | None      # None: unknown
    long_case: bool
    basis: str


def team_medians(world: World) -> dict[str, float | None]:
    calls = [d for p in world.physicians.values() for d in p.call_durations if d > 0]
    videos = [d for p in world.physicians.values() for d in p.video_durations if d > 0]
    notes = [d for p in world.physicians.values() for d in p.note_durations if d > 0]
    return {"call": median(calls) if calls else None, "video": median(videos) if videos else None,
            "note": median(notes) if notes else None}


def free_in(p, world: World, team: dict) -> FreeIn:
    if not p.on_shift or p.routable:
        return FreeIn(0 if p.on_shift else None, False, "free now" if p.on_shift else "off shift")
    a = p.current_activity
    if p.verdict == "cooldown" and p.last_call_end_at:
        left = 2 - (world.now - p.last_call_end_at).total_seconds() / 60
        return FreeIn(max(0, int(-(-left // 1))), False, "2-minute cooldown")
    if not a or a["kind"] not in ("video", "incoming_call", "outgoing_call"):
        return FreeIn(None, False, "no Synapse activity to estimate from")
    kind = "video" if a["kind"] == "video" else "call"
    own = p.video_durations if kind == "video" else p.call_durations
    typical = median(own) if len(own) >= 3 else team.get(kind)
    basis = f"{'their' if len(own) >= 3 else 'team'} typical {kind} of {typical:.0f} min" if typical else "no history"
    if not typical:
        return FreeIn(None, False, basis)
    elapsed = (world.now - a["since"]).total_seconds() / 60
    if elapsed > 2 * typical:
        return FreeIn(None, True, f"long case: {elapsed:.0f} min so far, over twice the {basis}")
    return FreeIn(max(1, int(round(typical - elapsed))), False, basis)
