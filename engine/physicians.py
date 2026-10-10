"""Physician activity state machine and availability engine.

Availability is inferred from signals, never taken from self-reported status
alone. Each physician gets two things:
  - derived_state: a label in the Sevaro snapshot vocabulary
  - verdict: whether routing can use them right now, with a plain reason
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .model import Event, Physician, ShiftWindow
from .timeutil import fmt_clock, minutes_between

AVAILABLE = "Available"
IN_MEETING = "In Meeting"
TEMP_UNAVAILABLE = "Temporarily Unavailable"

# Snapshot vocabulary for derived_state.
LABEL_VIDEO = "Video Consult In Progress"
LABEL_INCOMING = "Incoming Hospital Call"
LABEL_OUTGOING = "Outgoing Hospital Call"
LABEL_CAMERA = "Camera Active - No Synapse Session"
LABEL_DOCUMENTATION = "Documentation / Note"
LABEL_TEMP_UNAVAILABLE = "Temporarily Unavailable"
LABEL_IN_MEETING = "In Meeting"
LABEL_AVAILABLE = "Available"
LABEL_OFF_SHIFT = "Off Shift"

# Verdict codes. Routable ones can take a new call under routing rule 2.
VERDICTS = {
    "busy": ("Busy", False),
    "probably_busy": ("Probably busy (flagged)", False),
    "unavailable": ("Unavailable", False),
    "ringing": ("Phone ringing", False),
    "cooldown": ("Cooling down", False),
    "off_shift": ("Off shift", False),
    "in_meeting": ("Available, in meeting", True),
    "unverified": ("Available, unverified", True),
    "available": ("Available", True),
}

STATUS_PATTERN = re.compile(r"status:\s*(.+)$", re.IGNORECASE)


def parse_status(description: str) -> tuple[str, bool]:
    """Returns (normalized status, recognized)."""
    match = STATUS_PATTERN.search(description or "")
    raw = (match.group(1) if match else description or "").strip().rstrip(".")
    low = raw.lower()
    if "unavailable" in low or "away" in low or "do not disturb" in low:
        return TEMP_UNAVAILABLE, True
    if "meeting" in low:
        return IN_MEETING, True
    if "available" in low:
        return AVAILABLE, True
    return raw or AVAILABLE, False


@dataclass
class PhysicianState:
    id: str
    name: str
    windows: list[ShiftWindow] = field(default_factory=list)
    started_windows: set[int] = field(default_factory=set)   # indexes of windows with a shift_started signal
    self_status: str = AVAILABLE
    status_since: datetime | None = None
    status_unrecognized: str | None = None
    agent_online: bool = True
    agent_offline_since: datetime | None = None
    agent_assumed_back: bool = False
    camera_active: bool = False
    camera_since: datetime | None = None
    active_calls: dict[tuple[str, str], dict] = field(default_factory=dict)   # (direction, consult) -> info
    active_videos: dict[str, dict] = field(default_factory=dict)              # consult -> info
    ringing: dict | None = None
    missed_rings: list[dict] = field(default_factory=list)
    last_call_end_at: datetime | None = None
    last_video_end_at: datetime | None = None
    last_answered_at: datetime | None = None
    last_signal_at: datetime | None = None
    assignments: list[tuple[str, datetime]] = field(default_factory=list)
    call_starts: list[tuple[datetime, str, str]] = field(default_factory=list)
    call_durations: list[float] = field(default_factory=list)
    video_durations: list[float] = field(default_factory=list)
    note_durations: list[float] = field(default_factory=list)
    open_notes_started: dict[str, datetime] = field(default_factory=dict)
    # Filled in by finalize()
    shift: ShiftWindow | None = None
    on_shift: bool = False
    derived_state: str = LABEL_AVAILABLE
    verdict: str = "available"
    verdict_reason: str = ""
    flags: list[str] = field(default_factory=list)
    consults_this_shift: int = 0
    calls_this_shift: int = 0
    open_consult_ids: list[str] = field(default_factory=list)
    minutes_to_shift_end: int | None = None
    current_activity: dict | None = None

    @property
    def verdict_label(self) -> str:
        return VERDICTS[self.verdict][0]

    @property
    def routable(self) -> bool:
        return VERDICTS[self.verdict][1]

    def window_at(self, t: datetime) -> ShiftWindow | None:
        for w in self.windows:
            if w.contains(t):
                return w
        return None

    def latest_window_before(self, t: datetime) -> ShiftWindow | None:
        past = [w for w in self.windows if w.start <= t]
        return max(past, key=lambda w: w.start) if past else None


def init_physician_states(physicians: dict[str, Physician]) -> dict[str, PhysicianState]:
    states = {}
    for pid, info in physicians.items():
        states[pid] = PhysicianState(pid, info.name, windows=[ShiftWindow(w.start, w.end, w.kind)
                                                              for w in info.scheduled_shifts])
    return states


def apply_physician_event(states: dict[str, PhysicianState], ev: Event, cfg) -> None:
    p = states.get(ev.physician_id)
    if p is None:
        return
    t = ev.event_time
    etype = ev.event_type
    p.last_signal_at = t if p.last_signal_at is None else max(p.last_signal_at, t)

    if etype == "physician_status_changed":
        status, recognized = parse_status(ev.description)
        p.self_status, p.status_since = status, t
        p.status_unrecognized = None if recognized else status
    elif etype == "agent_heartbeat_lost":
        p.agent_online, p.agent_offline_since = False, t
        p.camera_active = False  # camera state unknown while the agent is offline
    elif etype == "agent_heartbeat_restored":
        p.agent_online, p.agent_offline_since = True, None
    elif etype in ("camera_active", "camera_inactive"):
        if not p.agent_online:
            # A camera report means the desktop agent is reporting again.
            p.agent_online, p.agent_offline_since, p.agent_assumed_back = True, None, True
        p.camera_active = etype == "camera_active"
        p.camera_since = t
    elif etype == "incoming_call_ringing":
        p.ringing = {"consult_id": ev.consult_id, "hospital_id": ev.hospital_id, "since": t}
    elif etype in ("incoming_call_started", "outgoing_call_started"):
        direction = "incoming" if etype.startswith("incoming") else "outgoing"
        p.active_calls[(direction, ev.consult_id)] = {"direction": direction, "consult_id": ev.consult_id,
                                                      "hospital_id": ev.hospital_id, "since": t}
        p.call_starts.append((t, direction, ev.consult_id))
        if direction == "incoming":
            p.last_answered_at = t
            p.ringing = None
    elif etype in ("incoming_call_ended", "outgoing_call_ended"):
        direction = "incoming" if etype.startswith("incoming") else "outgoing"
        info = p.active_calls.pop((direction, ev.consult_id), None)
        if info is None:
            # End without a matching start for that consult: close the most recent call in that direction.
            keys = [k for k in p.active_calls if k[0] == direction]
            if keys:
                info = p.active_calls.pop(max(keys, key=lambda k: p.active_calls[k]["since"]))
        if info:
            p.call_durations.append((t - info["since"]).total_seconds() / 60)
        p.last_call_end_at = t
        if direction == "incoming":
            p.ringing = None
    elif etype == "video_started":
        p.active_videos[ev.consult_id] = {"consult_id": ev.consult_id, "hospital_id": ev.hospital_id, "since": t}
    elif etype == "video_ended":
        info = p.active_videos.pop(ev.consult_id, None)
        if info:
            p.video_durations.append((t - info["since"]).total_seconds() / 60)
        p.last_video_end_at = t
    elif etype == "physician_assigned":
        p.assignments.append((ev.consult_id, t))
    elif etype == "note_started":
        p.open_notes_started[ev.consult_id] = t
    elif etype == "note_signed":
        started = p.open_notes_started.pop(ev.consult_id, None)
        if started:
            p.note_durations.append((t - started).total_seconds() / 60)
    elif etype == "shift_started":
        _mark_shift_started(p, t, cfg)
    elif etype == "shift_ended":
        w = p.window_at(t)
        if w:
            w.end = t


def _mark_shift_started(p: PhysicianState, t: datetime, cfg) -> None:
    early = timedelta(minutes=60)
    for i, w in enumerate(p.windows):
        if w.start - early <= t < w.end:
            p.started_windows.add(i)
            return
    hours = float(cfg.get_path("shifts.default_shift_hours", 12))
    p.windows.append(ShiftWindow(t, t + timedelta(hours=hours), "joined"))
    p.started_windows.add(len(p.windows) - 1)


def finalize_physician(p: PhysicianState, now: datetime, consults: dict, cfg) -> None:
    """Compute shift, counts, derived label and availability verdict at `now`."""
    p.shift = p.window_at(now)
    p.on_shift = p.shift is not None
    window = p.shift or p.latest_window_before(now)
    if window:
        p.consults_this_shift = len({cid for cid, t in p.assignments if window.start <= t <= now})
        p.calls_this_shift = sum(1 for t, _, _ in p.call_starts if window.start <= t <= now)
        p.minutes_to_shift_end = minutes_between(now, window.end) if p.on_shift else None
    p.open_consult_ids = sorted(cid for cid, c in consults.items() if c.is_open and c.physician_id == p.id)

    ring_timeout = float(cfg.get_path("events.ringing_timeout_minutes", 3))
    if p.ringing and (now - p.ringing["since"]).total_seconds() / 60 > ring_timeout:
        p.missed_rings.append(p.ringing)
        p.ringing = None

    documenting = [cid for cid in p.open_consult_ids if consults[cid].stage == "Documentation"]
    video = max(p.active_videos.values(), key=lambda v: v["since"], default=None)
    call = max(p.active_calls.values(), key=lambda v: v["since"], default=None)
    camera_no_session = p.agent_online and p.camera_active and not p.active_videos

    # Derived label, in the Sevaro snapshot vocabulary.
    if not p.on_shift:
        label = LABEL_OFF_SHIFT
    elif video:
        label = LABEL_VIDEO
    elif call:
        label = LABEL_INCOMING if call["direction"] == "incoming" else LABEL_OUTGOING
    elif p.self_status == TEMP_UNAVAILABLE:
        label = LABEL_TEMP_UNAVAILABLE
    elif camera_no_session and p.self_status != IN_MEETING:
        label = LABEL_CAMERA
    elif p.self_status == IN_MEETING:
        label = LABEL_IN_MEETING
    elif documenting:
        label = LABEL_DOCUMENTATION
    else:
        label = LABEL_AVAILABLE
    p.derived_state = label

    activity = None
    if video:
        activity = {"kind": "video", **video}
    elif call:
        activity = {"kind": f"{call['direction']}_call", **call}
    elif documenting:
        activity = {"kind": "note", "consult_id": documenting[0], "since": p.open_notes_started.get(documenting[0])}
    p.current_activity = activity

    # Availability verdict with a plain reason.
    flags = []
    cooldown = float(cfg.get_path("routing.cooldown_minutes", 2))
    since = fmt_clock(p.agent_offline_since)
    if not p.agent_online:
        flags.append("agent_offline")
    if camera_no_session and p.self_status != IN_MEETING:
        flags.append("camera_no_session")
    if p.ringing:
        flags.append("ringing")
    if p.self_status == TEMP_UNAVAILABLE and p.open_consult_ids:
        flags.append("unavailable_with_open_consults")
    if p.status_unrecognized:
        flags.append("unrecognized_status")
    if p.agent_assumed_back:
        flags.append("agent_assumed_back")
    if documenting:
        flags.append("writing_note")

    if not p.on_shift:
        verdict, reason = "off_shift", "Not on shift."
    elif video:
        verdict, reason = "busy", f"On a Synapse video consult ({video['consult_id']}) since {fmt_clock(video['since'])}."
    elif call:
        verdict, reason = "busy", (f"On an {call['direction']} hospital call"
                                   f"{' for ' + call['consult_id'] if call['consult_id'] else ''} since {fmt_clock(call['since'])}.")
    elif p.self_status == TEMP_UNAVAILABLE:
        verdict, reason = "unavailable", f"Set status to Temporarily Unavailable at {fmt_clock(p.status_since)}."
    elif camera_no_session and p.self_status != IN_MEETING:
        verdict, reason = "probably_busy", (f"Camera on since {fmt_clock(p.camera_since)} with no Synapse video. "
                                            "Probably on an outside video platform.")
    elif p.ringing:
        verdict, reason = "ringing", (f"Phone ringing for {p.ringing['consult_id'] or 'a call'} "
                                      f"since {fmt_clock(p.ringing['since'])}.")
    elif p.last_call_end_at and (now - p.last_call_end_at).total_seconds() / 60 < cooldown:
        verdict, reason = "cooldown", f"Ended a call at {fmt_clock(p.last_call_end_at)}, less than {cooldown:g} minutes ago."
    elif p.self_status == IN_MEETING:
        verdict, reason = "in_meeting", "In an internal meeting. Meetings can be interrupted."
    elif not p.agent_online:
        verdict, reason = "unverified", (f"Desktop agent stopped reporting at {since}. Routing assumes available, "
                                         "but this is not confirmed.")
    elif documenting:
        verdict, reason = "available", f"Writing a note for {documenting[0]}. Can be interrupted."
    else:
        verdict, reason = "available", "No active call, video, or conflicting signal."
    if not p.agent_online and verdict not in ("unverified", "off_shift"):
        reason += f" Desktop agent offline since {since}."
    p.verdict, p.verdict_reason, p.flags = verdict, reason, flags
