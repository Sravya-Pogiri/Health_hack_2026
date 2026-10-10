"""Event normalization: validation, dedupe, ordering, late events, re-calls.

State is never built from arrival order. Events are sorted by
(event_time, ingested_at, event_id) and replayed from scratch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .model import Event
from .timeutil import fmt_clock, parse_ts

BRIEF_EVENT_TYPES = {
    "consult_received", "consult_triaged", "physician_assigned",
    "incoming_call_ringing", "incoming_call_started", "incoming_call_ended",
    "outgoing_call_started", "outgoing_call_ended",
    "imaging_requested", "imaging_returned", "video_started", "video_ended",
    "note_started", "note_signed", "routing_attempt_failed",
    "physician_status_changed", "camera_active", "camera_inactive",
    "agent_heartbeat_lost", "shift_started",
}

# Types that only make sense with a consult or with a physician.
NEEDS_CONSULT = {
    "consult_received", "consult_triaged", "physician_assigned", "imaging_requested",
    "imaging_returned", "video_started", "video_ended", "note_started", "note_signed",
    "routing_attempt_failed", "consult_reclassified", "consult_cancelled",
}
NEEDS_PHYSICIAN = {
    "physician_assigned", "physician_status_changed", "camera_active", "camera_inactive",
    "agent_heartbeat_lost", "agent_heartbeat_restored", "shift_started", "shift_ended",
    "incoming_call_ringing", "incoming_call_started", "incoming_call_ended",
    "outgoing_call_started", "outgoing_call_ended", "video_started", "video_ended",
}

EVENT_LABELS = {
    "consult_received": "Consult request",
    "consult_triaged": "Triage",
    "physician_assigned": "Assignment",
    "incoming_call_ringing": "Incoming call ring",
    "incoming_call_started": "Incoming call start",
    "incoming_call_ended": "Incoming call end",
    "outgoing_call_started": "Outgoing call start",
    "outgoing_call_ended": "Outgoing call end",
    "imaging_requested": "Imaging request",
    "imaging_returned": "Imaging result",
    "video_started": "Video start",
    "video_ended": "Video end",
    "note_started": "Note start",
    "note_signed": "Note sign-off",
    "routing_attempt_failed": "Failed routing attempt",
    "physician_status_changed": "Status change",
    "camera_active": "Camera on",
    "camera_inactive": "Camera off",
    "agent_heartbeat_lost": "Desktop agent loss",
    "agent_heartbeat_restored": "Desktop agent return",
    "shift_started": "Shift start",
    "shift_ended": "Shift end",
}

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]")


def content_key(ev: Event) -> tuple:
    return (ev.event_time, ev.consult_id, ev.event_type, ev.physician_id, ev.hospital_id, ev.description)


def sort_key(ev: Event) -> tuple:
    return (ev.event_time, ev.ingested_at, ev.event_id)


@dataclass
class AppendReport:
    source: str
    rows_read: int = 0
    added: int = 0
    duplicates: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    late_events: list[dict] = field(default_factory=list)
    recalls: list[dict] = field(default_factory=list)
    unknown_physicians: list[str] = field(default_factory=list)
    unknown_hospitals: list[str] = field(default_factory=list)
    new_consults: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    latest_ingested: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "rows_read": self.rows_read,
            "added": self.added,
            "duplicates_skipped": len(self.duplicates),
            "duplicates": self.duplicates,
            "conflicts": self.conflicts,
            "skipped": self.skipped,
            "late_events": self.late_events,
            "recalls": self.recalls,
            "unknown_physicians": self.unknown_physicians,
            "unknown_hospitals": self.unknown_hospitals,
            "new_consults": self.new_consults,
            "missing_columns": self.missing_columns,
            "latest_ingested": self.latest_ingested.isoformat() if self.latest_ingested else None,
        }


class EventLog:
    """All events loaded so far (kit plus appended files), deduplicated and ordered."""

    def __init__(self, cfg, known_physicians: set[str], known_hospitals: set[str]):
        self.cfg = cfg
        self.known_physicians = known_physicians
        self.known_hospitals = known_hospitals
        self.allowed_types = BRIEF_EVENT_TYPES | set(cfg.get_path("events.extension_types", []) or [])
        self.late_threshold = float(cfg.get_path("events.late_threshold_seconds", 60))
        self._by_id: dict[str, Event] = {}
        self._by_content: dict[tuple, Event] = {}
        self._events: list[Event] = []
        self._seq = 0
        self.reports: list[AppendReport] = []
        # new consult_id -> existing consult_id it explicitly says it repeats
        self.consult_aliases: dict[str, str] = {}

    @property
    def events(self) -> list[Event]:
        return self._events

    def latest_ingested(self) -> datetime | None:
        return max((e.ingested_at for e in self._events), default=None)

    def consult_ids(self) -> set[str]:
        return {e.consult_id for e in self._events if e.consult_id}

    def add_rows(self, columns: list[str], rows: list[dict], source: str) -> AppendReport:
        from .loader import EVENT_COLUMNS

        report = AppendReport(source=source, rows_read=len(rows))
        missing = [c for c in EVENT_COLUMNS if c not in columns]
        if missing:
            report.missing_columns = missing
            self.reports.append(report)
            return report

        existing_consults = self.consult_ids()
        received_before = {e.consult_id: e for e in self._events if e.event_type == "consult_received"}
        parsed: list[tuple[int, Event]] = []
        for index, row in enumerate(rows, start=2):
            ev = self._parse_row(row, index, source, report)
            if ev is not None:
                parsed.append((index, ev))

        # Within one file, the earliest-arriving copy of a duplicate wins.
        parsed.sort(key=lambda item: (item[1].ingested_at, item[0]))
        added: list[Event] = []
        unknown_p, unknown_h = set(), set()
        for index, ev in parsed:
            prior = self._by_id.get(ev.event_id)
            if prior is not None:
                if content_key(prior) == content_key(ev):
                    report.duplicates.append({"row": index, "event_id": ev.event_id, "kind": "same event_id",
                                              "kept_ingested_at": prior.ingested_at.isoformat()})
                else:
                    report.conflicts.append({"row": index, "event_id": ev.event_id,
                                             "reason": "event_id already used for a different event; kept the first"})
                continue
            prior = self._by_content.get(content_key(ev))
            if prior is not None:
                report.duplicates.append({"row": index, "event_id": ev.event_id, "kind": "same content",
                                          "duplicate_of": prior.event_id})
                continue
            self._seq += 1
            ev = Event(**{**ev.__dict__, "seq": self._seq})
            self._by_id[ev.event_id] = ev
            self._by_content[content_key(ev)] = ev
            added.append(ev)
            if ev.physician_id and ev.physician_id not in self.known_physicians:
                unknown_p.add(ev.physician_id)
            if ev.hospital_id and ev.hospital_id not in self.known_hospitals:
                unknown_h.add(ev.hospital_id)

        self._events.extend(added)
        self._events.sort(key=sort_key)
        report.added = len(added)
        report.unknown_physicians = sorted(unknown_p)
        report.unknown_hospitals = sorted(unknown_h)
        report.latest_ingested = max((e.ingested_at for e in added), default=None)

        for ev in added:
            if ev.lag_seconds > self.late_threshold:
                label = EVENT_LABELS.get(ev.event_type, ev.event_type)
                subject = f" for {ev.consult_id}" if ev.consult_id else (f" for {ev.physician_id}" if ev.physician_id else "")
                report.late_events.append({
                    "event_id": ev.event_id,
                    "lag_seconds": int(ev.lag_seconds),
                    "message": f"{label}{subject} happened at {fmt_clock(ev.event_time)} and arrived at "
                               f"{fmt_clock(ev.ingested_at)}. Placed at {fmt_clock(ev.event_time)}.",
                })

        report.recalls = self._detect_recalls(added, received_before, existing_consults)
        recall_event_ids = {r["event_id"] for r in report.recalls}
        report.new_consults = sorted({e.consult_id for e in added
                                      if e.event_type == "consult_received" and e.consult_id not in existing_consults
                                      and e.event_id not in recall_event_ids})
        self.reports.append(report)
        return report

    def _detect_recalls(self, added: list[Event], received_before: dict[str, Event],
                        existing_consults: set[str]) -> list[dict]:
        """A repeat consult_received for a consult we already have is the same patient calling again."""
        recalls = []
        first_received = dict(received_before)
        for ev in sorted(added, key=sort_key):
            if ev.event_type != "consult_received":
                continue
            original = first_received.get(ev.consult_id)
            if original is not None and original.event_id != ev.event_id:
                recalls.append({"event_id": ev.event_id, "consult_id": ev.consult_id, "kind": "same consult_id",
                                "message": f"{ev.consult_id} called again at {fmt_clock(ev.event_time)}. "
                                           f"Counted as the same patient, not new demand."})
                continue
            # A new consult_id whose description names an existing consult at the same hospital
            # is an explicit repeat. We never merge on a guess.
            referenced = [tok for tok in TOKEN_PATTERN.findall(ev.description or "")
                          if tok != ev.consult_id and tok in first_received]
            linked = None
            for cid in referenced:
                orig = first_received.get(cid)
                if orig is not None and orig.hospital_id == ev.hospital_id:
                    linked = cid
                    break
            if linked:
                self.consult_aliases[ev.consult_id] = linked
                recalls.append({"event_id": ev.event_id, "consult_id": linked, "kind": "explicit reference",
                                "message": f"{ev.consult_id} says it repeats {linked}. Counted as the same patient."})
                continue
            if original is None:
                first_received[ev.consult_id] = ev
        return recalls

    def _parse_row(self, row: dict, index: int, source: str, report: AppendReport) -> Event | None:
        event_id = (row.get("event_id") or "").strip()
        etype = (row.get("event_type") or "").strip()
        if not event_id:
            report.skipped.append({"row": index, "reason": "missing event_id"})
            return None
        try:
            event_time = parse_ts(row.get("event_time", ""))
            ingested_at = parse_ts(row.get("ingested_at", ""))
        except ValueError:
            report.skipped.append({"row": index, "event_id": event_id, "reason": "malformed timestamp"})
            return None
        if etype not in self.allowed_types:
            report.skipped.append({"row": index, "event_id": event_id, "reason": f"unknown event type '{etype}'"})
            return None
        consult_id = (row.get("consult_id") or "").strip()
        physician_id = (row.get("physician_id") or "").strip()
        if etype in NEEDS_CONSULT and not consult_id:
            report.skipped.append({"row": index, "event_id": event_id, "reason": f"{etype} without consult_id"})
            return None
        if etype in NEEDS_PHYSICIAN and not physician_id:
            report.skipped.append({"row": index, "event_id": event_id, "reason": f"{etype} without physician_id"})
            return None
        return Event(
            event_id=event_id, event_time=event_time, ingested_at=ingested_at, consult_id=consult_id,
            event_type=etype, physician_id=physician_id, hospital_id=(row.get("hospital_id") or "").strip(),
            description=(row.get("description") or "").strip(), source=source,
        )
