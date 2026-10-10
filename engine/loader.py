"""Reads the developer kit files, validates them, and returns typed records.

Bad rows never crash the loader: they are skipped and listed in the load report.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .model import BaselineRow, CoverageBlock, Hospital, Physician, ShiftWindow
from .timeutil import ET, at_et, parse_hhmm, parse_ts

EVENT_COLUMNS = [
    "event_id", "event_time", "ingested_at", "consult_id",
    "event_type", "physician_id", "hospital_id", "description",
]

REQUIRED_COLUMNS = {
    "hospitals.csv": ["hospital_id", "hospital_name", "local_timezone", "coverage_tier"],
    "physicians_on_shift.csv": ["physician_id", "name", "shift_start_et", "shift_end_et"],
    "physicians_off_shift_roster.csv": [
        "physician_id", "name", "role", "split_shift_opt_in", "last_shift_end",
        "next_shift_start", "hours_worked_last_7d", "est_minutes_to_online",
    ],
    "credentialing_matrix.csv": ["physician_id"],
    "baseline_hourly_volume.csv": ["hospital_id", "hour_start_et", "mean_consults", "p90_consults"],
    "coverage_requirements.csv": ["hospital_id", "time_block_et", "min_available_credentialed_physicians"],
    "event_stream.csv": EVENT_COLUMNS,
}


@dataclass
class LoadReport:
    files: list[dict] = field(default_factory=list)
    skipped_rows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def skip(self, file: str, row: int, reason: str) -> None:
        self.skipped_rows.append({"file": file, "row": row, "reason": reason})

    def to_dict(self) -> dict:
        return {"files": self.files, "skipped_rows": self.skipped_rows, "warnings": self.warnings}


@dataclass
class Reference:
    hospitals: dict[str, Hospital]
    physicians: dict[str, Physician]
    credentials: dict[str, set[str]]             # physician_id -> hospital_ids
    baseline: dict[tuple[str, int], BaselineRow]  # (hospital_id, hour) -> row
    coverage: dict[str, list[CoverageBlock]]     # hospital_id -> blocks
    scenario_date: date | None
    report: LoadReport


def read_csv_text(text: str) -> tuple[list[str], list[dict]]:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    columns = [c.strip() for c in (reader.fieldnames or [])]
    rows = []
    for raw in reader:
        rows.append({(k or "").strip(): (v or "").strip() if isinstance(v, str) else "" for k, v in raw.items()})
    return columns, rows


def _read_file(path: Path, report: LoadReport, required: list[str]) -> list[dict]:
    name = path.name
    if not path.exists():
        report.files.append({"name": name, "status": "missing", "rows": 0})
        report.warnings.append(f"{name} was not found.")
        return []
    columns, rows = read_csv_text(path.read_text(encoding="utf-8-sig"))
    missing = [c for c in required if c not in columns]
    entry = {"name": name, "rows": len(rows), "status": "ok" if not missing else "missing_columns"}
    if missing:
        entry["missing_columns"] = missing
        report.warnings.append(f"{name} is missing columns: {', '.join(missing)}.")
    report.files.append(entry)
    return [] if missing else rows


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _yes(value: str) -> bool | None:
    v = (value or "").strip().lower()
    if v in ("yes", "y", "true", "1"):
        return True
    if v in ("no", "n", "false", "0"):
        return False
    return None


def scenario_date_from_rows(event_rows: list[dict]) -> date | None:
    earliest: datetime | None = None
    for row in event_rows:
        try:
            t = parse_ts(row.get("event_time", ""))
        except ValueError:
            continue
        if earliest is None or t < earliest:
            earliest = t
    return earliest.astimezone(ET).date() if earliest else None


def load_reference(kit_dir: Path, cfg, event_rows: list[dict], report: LoadReport) -> Reference:
    """Load every reference file. event_rows are only used to find the scenario date."""
    scenario = scenario_date_from_rows(event_rows)
    if scenario is None:
        report.warnings.append("No valid event times found; scenario date unknown.")

    hospitals: dict[str, Hospital] = {}
    for i, row in enumerate(_read_file(kit_dir / "hospitals.csv", report, REQUIRED_COLUMNS["hospitals.csv"]), start=2):
        hid = row.get("hospital_id", "")
        if not hid:
            report.skip("hospitals.csv", i, "missing hospital_id")
            continue
        hospitals[hid] = Hospital(hid, row.get("hospital_name", hid), row.get("local_timezone") or "America/New_York",
                                  row.get("coverage_tier", ""))

    physicians: dict[str, Physician] = {}
    default_hours = float(cfg.get_path("shifts.default_shift_hours", 12))
    night_hours = float(cfg.get_path("shifts.night_shift_hours", 12))
    night_start = int(cfg.get_path("shifts.night_start_hour", 19))

    for i, row in enumerate(_read_file(kit_dir / "physicians_on_shift.csv", report,
                                       REQUIRED_COLUMNS["physicians_on_shift.csv"]), start=2):
        pid = row.get("physician_id", "")
        if not pid:
            report.skip("physicians_on_shift.csv", i, "missing physician_id")
            continue
        phys = Physician(pid, row.get("name", pid), in_on_shift_file=True)
        try:
            start_t = parse_hhmm(row["shift_start_et"])
            end_t = parse_hhmm(row["shift_end_et"])
        except (KeyError, ValueError):
            report.skip("physicians_on_shift.csv", i, "bad shift times")
            physicians[pid] = phys
            continue
        if scenario:
            start = at_et(scenario, start_t)
            end = at_et(scenario, end_t)
            if end <= start:
                end += timedelta(days=1)  # shift crosses midnight
            phys.scheduled_shifts.append(ShiftWindow(start, end, "scheduled"))
        phys.role = "Night" if start_t.hour >= night_start else "Day"
        physicians[pid] = phys

    for i, row in enumerate(_read_file(kit_dir / "physicians_off_shift_roster.csv", report,
                                       REQUIRED_COLUMNS["physicians_off_shift_roster.csv"]), start=2):
        pid = row.get("physician_id", "")
        if not pid:
            report.skip("physicians_off_shift_roster.csv", i, "missing physician_id")
            continue
        phys = physicians.get(pid) or Physician(pid, row.get("name", pid))
        phys.in_roster = True
        phys.role = row.get("role") or phys.role
        phys.opt_in = _yes(row.get("split_shift_opt_in", ""))
        for key, attr in (("last_shift_end", "last_shift_end"), ("next_shift_start", "next_shift_start")):
            try:
                setattr(phys, attr, parse_ts(row.get(key, "")))
            except ValueError:
                report.skip("physicians_off_shift_roster.csv", i, f"bad {key}; field left empty")
        phys.hours_worked_last_7d = _to_float(row.get("hours_worked_last_7d", ""))
        phys.est_minutes_to_online = _to_int(row.get("est_minutes_to_online", ""))
        phys.preferred_channel = row.get("preferred_channel") or None
        phys.offers_declined_last_30d = _to_int(row.get("offers_declined_last_30d", ""))
        if phys.next_shift_start:
            hours = night_hours if (phys.role or "").lower() == "night" else default_hours
            phys.scheduled_shifts.append(
                ShiftWindow(phys.next_shift_start, phys.next_shift_start + timedelta(hours=hours), "next_scheduled"))
        physicians[pid] = phys

    credentials: dict[str, set[str]] = {}
    for i, row in enumerate(_read_file(kit_dir / "credentialing_matrix.csv", report,
                                       REQUIRED_COLUMNS["credentialing_matrix.csv"]), start=2):
        pid = row.get("physician_id", "")
        if not pid:
            report.skip("credentialing_matrix.csv", i, "missing physician_id")
            continue
        creds = {col for col, val in row.items()
                 if col not in ("physician_id", "name") and val.strip().upper() in ("Y", "YES", "TRUE", "1")}
        unknown = sorted(c for c in creds if hospitals and c not in hospitals)
        if unknown:
            report.warnings.append(f"Credentialing lists unknown hospitals for {pid}: {', '.join(unknown)}.")
        credentials[pid] = creds
        if pid not in physicians:
            physicians[pid] = Physician(pid, row.get("name", pid))
            report.warnings.append(f"{pid} appears only in the credentialing matrix.")
    for pid in physicians:
        if pid not in credentials:
            credentials[pid] = set()
            report.warnings.append(f"{pid} has no credentialing row; treated as credentialed nowhere.")

    baseline: dict[tuple[str, int], BaselineRow] = {}
    for i, row in enumerate(_read_file(kit_dir / "baseline_hourly_volume.csv", report,
                                       REQUIRED_COLUMNS["baseline_hourly_volume.csv"]), start=2):
        dow = (row.get("dow_type") or "weekday").lower()
        if scenario and dow in ("weekday", "weekend"):
            is_weekend = scenario.weekday() >= 5
            if (dow == "weekend") != is_weekend:
                continue
        try:
            hour = parse_hhmm(row["hour_start_et"]).hour
        except (KeyError, ValueError):
            report.skip("baseline_hourly_volume.csv", i, "bad hour_start_et")
            continue
        mean, p90 = _to_float(row.get("mean_consults", "")), _to_float(row.get("p90_consults", ""))
        if mean is None or p90 is None:
            report.skip("baseline_hourly_volume.csv", i, "bad mean or p90")
            continue
        baseline[(row["hospital_id"], hour)] = BaselineRow(row["hospital_id"], hour, mean, p90,
                                                           _to_int(row.get("weeks_sampled", "")))
    for hid in hospitals:
        missing_hours = [h for h in range(24) if (hid, h) not in baseline]
        if missing_hours:
            report.warnings.append(f"Baseline missing {len(missing_hours)} hour(s) for {hid}; surge checks skip those hours.")

    coverage: dict[str, list[CoverageBlock]] = {}
    for i, row in enumerate(_read_file(kit_dir / "coverage_requirements.csv", report,
                                       REQUIRED_COLUMNS["coverage_requirements.csv"]), start=2):
        label = row.get("time_block_et", "")
        try:
            start_s, end_s = label.split("-")
            s, e = parse_hhmm(start_s), parse_hhmm(end_s)
            required = int(float(row["min_available_credentialed_physicians"]))
        except (ValueError, KeyError):
            report.skip("coverage_requirements.csv", i, "bad time block or requirement")
            continue
        coverage.setdefault(row["hospital_id"], []).append(
            CoverageBlock(row["hospital_id"], s.hour * 60 + s.minute, e.hour * 60 + e.minute, required, label))
    for hid in hospitals:
        if hid not in coverage:
            report.warnings.append(f"No coverage requirement for {hid}; assumed 1 physician.")

    return Reference(hospitals, physicians, credentials, baseline, coverage, scenario, report)


def read_event_file(path: Path) -> tuple[list[str], list[dict]]:
    return read_csv_text(path.read_text(encoding="utf-8-sig"))
