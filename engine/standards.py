"""Operational time standards: which clock applies to a consult, and how far along it is."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .consults import ConsultState
from .physicians import PhysicianState

STANDARD_NAMES = {
    "triaged_unassigned": "Triaged, not yet assigned",
    "assigned_not_contacted": "Assigned, physician not yet contacted",
    "imaging_pending": "CT/CTA or imaging pending",
    "imaging_review": "Imaging back, video not started",
    "note_stroke": "Note not signed after the call or video (stroke)",
    "note_general": "Note not signed after the call or video (general neurology)",
    "received_not_triaged": "Received, not yet triaged",
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "none": 3}


@dataclass
class StandardCheck:
    key: str
    name: str
    status: str            # none | warning | critical | info
    started_at: datetime
    elapsed_minutes: float
    warning: float | None
    critical: float | None

    @property
    def minutes(self) -> int:
        return int(self.elapsed_minutes)

    @property
    def warning_at(self) -> datetime | None:
        return self.started_at + timedelta(minutes=self.warning) if self.warning is not None else None

    @property
    def critical_at(self) -> datetime | None:
        return self.started_at + timedelta(minutes=self.critical) if self.critical is not None else None

    @property
    def minutes_to_critical(self) -> int | None:
        if self.critical is None or self.status == "critical":
            return None
        return max(0, int(-(-(self.critical - self.elapsed_minutes) // 1)))

    @property
    def minutes_to_warning(self) -> int | None:
        if self.warning is None or self.status in ("warning", "critical"):
            return None
        return max(0, int(-(-(self.warning - self.elapsed_minutes) // 1)))

    @property
    def minutes_over(self) -> float:
        limit = self.critical if self.status == "critical" else self.warning
        return self.elapsed_minutes - limit if limit is not None else 0.0

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "status": self.status, "minutes": self.minutes,
            "started_at": self.started_at.isoformat(), "warning": self.warning, "critical": self.critical,
            "goes_warning_in": self.minutes_to_warning, "goes_critical_in": self.minutes_to_critical,
        }


def _applies(rule: dict, consult: ConsultState) -> bool:
    scope = rule.get("applies_to", "all")
    if scope == "all":
        return True
    if scope == "stroke":
        return consult.is_stroke
    return not consult.is_stroke


def _make(key: str, rule: dict | None, started: datetime, now: datetime) -> StandardCheck:
    elapsed = max(0.0, (now - started).total_seconds() / 60)
    warning = float(rule["warning"]) if rule else None
    critical = float(rule["critical"]) if rule else None
    status = "none"
    if critical is not None and elapsed >= critical:
        status = "critical"
    elif warning is not None and elapsed >= warning:
        status = "warning"
    return StandardCheck(key, STANDARD_NAMES.get(key, key), status, started, elapsed, warning, critical)


def check_consult(consult: ConsultState, now: datetime, cfg) -> StandardCheck | None:
    """The time standard that applies to the consult's current stage, if any."""
    if not consult.is_open or consult.stage_entered_at is None:
        return None
    rules = cfg.get_path("standards", {}) or {}
    stage = consult.stage
    key = None
    started = consult.stage_entered_at
    if stage == "Triaged" and not consult.physician_id:
        key = "triaged_unassigned"
    elif stage == "Assigned":
        key = "assigned_not_contacted"
    elif stage == "CT/CTA or Imaging Pending":
        key = "imaging_pending"
    elif stage == "Imaging Review":
        key = "imaging_review"
    elif stage in ("Note Pending", "Documentation"):
        key = "note_stroke" if consult.is_stroke else "note_general"
        started = consult.clinical_end_at or consult.stage_entered_at
    elif stage == "Received":
        info_after = float(rules.get("received_not_triaged_info_minutes", 5))
        check = _make("received_not_triaged", None, started, now)
        check.status = "info" if check.elapsed_minutes >= info_after else "none"
        return check
    if key is None:
        return None
    rule = rules.get(key)
    if not rule or not _applies(rule, consult):
        return None
    return _make(key, rule, started, now)


@dataclass
class OpenNotesCheck:
    physician_id: str
    status: str             # none | warning | critical
    consult_ids: list[str]
    minutes_to_shift_end: int


def check_open_notes(p: PhysicianState, consults: dict[str, ConsultState], cfg) -> OpenNotesCheck | None:
    """Open notes within the configured window before the physician's shift ends."""
    rule = cfg.get_path("standards.open_notes_near_shift_end", {}) or {}
    window = float(rule.get("window_minutes", 60))
    if not p.on_shift or p.minutes_to_shift_end is None or p.minutes_to_shift_end > window:
        return None
    notes = [cid for cid in p.open_consult_ids if consults[cid].stage in ("Note Pending", "Documentation")]
    if not notes:
        return None
    status = "critical" if len(notes) >= int(rule.get("critical_count", 2)) else (
        "warning" if len(notes) >= int(rule.get("warning_count", 1)) else "none")
    return OpenNotesCheck(p.id, status, notes, p.minutes_to_shift_end)
