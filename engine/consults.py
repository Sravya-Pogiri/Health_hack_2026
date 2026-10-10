"""Consult lifecycle state machine.

Consult state is tracked separately from physician activity. Stages only move
forward, so a later call (for example a call to check on imaging) never resets
a consult's stage or its clock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .model import Event

STAGES = [
    "Received",
    "Triaged",
    "Assigned",
    "Provider Contacted",
    "CT/CTA or Imaging Pending",
    "Imaging Review",
    "Video Consult In Progress",
    "Note Pending",
    "Documentation",
    "Signed Off",
]
STAGE_INDEX = {s: i for i, s in enumerate(STAGES)}
STAGE_INDEX["Cancelled"] = len(STAGES)
CLOSED_STAGES = {"Signed Off", "Cancelled"}

STROKE = "Stroke"
GENERAL = "General Neuro"


def parse_consult_type(description: str) -> str | None:
    text = (description or "").lower()
    if "not stroke" in text or "non-stroke" in text or "mimic" in text:
        return GENERAL
    if "general neuro" in text or "general neurology" in text:
        return GENERAL
    if "stroke" in text:
        return STROKE
    return None


PHONE_ONLY = re.compile(r"phone[- ]only", re.IGNORECASE)


@dataclass
class ConsultState:
    consult_id: str
    hospital_id: str
    consult_type: str = STROKE
    type_known: bool = False
    received_at: datetime | None = None
    missing_received: bool = False
    triaged_at: datetime | None = None
    physician_id: str | None = None
    assigned_at: datetime | None = None
    assignment_history: list[tuple[str, datetime]] = field(default_factory=list)
    implicit_assignment: bool = False
    contacted_at: datetime | None = None
    imaging_requested_at: datetime | None = None
    imaging_returned_at: datetime | None = None
    imaging_kind: str | None = None
    video_started_at: datetime | None = None
    video_ended_at: datetime | None = None
    clinical_end_at: datetime | None = None
    note_started_at: datetime | None = None
    signed_at: datetime | None = None
    cancelled_at: datetime | None = None
    stage: str = "Received"
    stage_entered_at: datetime | None = None
    stage_history: list[tuple[str, datetime]] = field(default_factory=list)
    phone_only: bool = False
    call_active: bool = False
    last_call_end_at: datetime | None = None
    recall_times: list[datetime] = field(default_factory=list)
    routing_failures: list[datetime] = field(default_factory=list)
    ringing_to: str | None = None
    ringing_since: datetime | None = None
    reclassifications: list[dict] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.stage not in CLOSED_STAGES

    @property
    def is_stroke(self) -> bool:
        return self.consult_type == STROKE

    @property
    def recall_count(self) -> int:
        return len(self.recall_times)

    @property
    def last_recall_at(self) -> datetime | None:
        return self.recall_times[-1] if self.recall_times else None

    def advance(self, stage: str, t: datetime) -> None:
        if STAGE_INDEX[stage] > STAGE_INDEX.get(self.stage, -1):
            self.stage = stage
            self.stage_entered_at = t
            self.stage_history.append((stage, t))


def apply_consult_event(consults: dict[str, ConsultState], ev: Event, aliases: dict[str, str]) -> None:
    """Apply one event (already in event-time order) to the consult it belongs to."""
    if not ev.consult_id:
        return
    cid = aliases.get(ev.consult_id, ev.consult_id)
    c = consults.get(cid)
    etype = ev.event_type
    t = ev.event_time

    if etype == "consult_received":
        if c is None:
            c = ConsultState(cid, ev.hospital_id, received_at=t, stage_entered_at=t)
            c.stage_history.append(("Received", t))
            parsed = parse_consult_type(ev.description)
            c.consult_type = parsed or STROKE
            c.type_known = parsed is not None
            consults[cid] = c
        else:
            # Same patient calling again: never a new consult, never a clock reset.
            c.recall_times.append(t)
            if not c.type_known:
                parsed = parse_consult_type(ev.description)
                if parsed:
                    c.consult_type, c.type_known = parsed, True
        c.event_ids.append(ev.event_id)
        return

    if c is None:
        # The consult's received event is missing. Start the clock at the first thing we saw.
        c = ConsultState(cid, ev.hospital_id, received_at=t, missing_received=True, stage_entered_at=t)
        c.stage_history.append(("Received", t))
        consults[cid] = c
    if not c.hospital_id and ev.hospital_id:
        c.hospital_id = ev.hospital_id
    c.event_ids.append(ev.event_id)

    if etype == "consult_triaged":
        c.triaged_at = c.triaged_at or t
        c.advance("Triaged", t)
    elif etype == "physician_assigned":
        _assign(c, ev.physician_id, t)
        c.advance("Assigned", t)
    elif etype == "incoming_call_ringing":
        c.ringing_to, c.ringing_since = ev.physician_id or None, t
    elif etype in ("incoming_call_started", "outgoing_call_started"):
        if ev.physician_id and not c.physician_id:
            _assign(c, ev.physician_id, t)
            c.implicit_assignment = True
        c.ringing_to = c.ringing_since = None
        c.call_active = True
        c.contacted_at = c.contacted_at or t
        if c.physician_id:
            c.advance("Provider Contacted", t)
    elif etype in ("incoming_call_ended", "outgoing_call_ended"):
        c.call_active = False
        c.last_call_end_at = t
        if PHONE_ONLY.search(ev.description or ""):
            c.phone_only = True
            c.clinical_end_at = c.clinical_end_at or t
            c.advance("Note Pending", t)
    elif etype == "imaging_requested":
        c.imaging_requested_at = c.imaging_requested_at or t
        c.imaging_kind = (ev.description or "").replace(" requested", "") or None
        c.advance("CT/CTA or Imaging Pending", t)
    elif etype == "imaging_returned":
        c.imaging_returned_at = c.imaging_returned_at or t
        c.advance("Imaging Review", t)
    elif etype == "video_started":
        c.video_started_at = c.video_started_at or t
        c.advance("Video Consult In Progress", t)
    elif etype == "video_ended":
        c.video_ended_at = t
        c.clinical_end_at = t
        c.advance("Note Pending", t)
    elif etype == "note_started":
        c.note_started_at = c.note_started_at or t
        if c.clinical_end_at is None:
            # Phone-only consult without an explicit marker: the note clock starts at the last call end.
            c.clinical_end_at = c.video_ended_at or c.last_call_end_at or t
        c.advance("Documentation", t)
    elif etype == "note_signed":
        c.signed_at = t
        c.advance("Signed Off", t)
    elif etype == "routing_attempt_failed":
        c.routing_failures.append(t)
    elif etype == "consult_reclassified":
        new_type = parse_consult_type(ev.description)
        if new_type and new_type != c.consult_type:
            c.reclassifications.append({"at": t, "from": c.consult_type, "to": new_type,
                                        "by": ev.physician_id or "unknown"})
            c.consult_type, c.type_known = new_type, True
    elif etype == "consult_cancelled":
        c.cancelled_at = t
        c.stage, c.stage_entered_at = "Cancelled", t
        c.stage_history.append(("Cancelled", t))


def _assign(c: ConsultState, physician_id: str, t: datetime) -> None:
    if not physician_id or physician_id == c.physician_id:
        return
    c.physician_id = physician_id
    c.assigned_at = c.assigned_at or t
    c.assignment_history.append((physician_id, t))
