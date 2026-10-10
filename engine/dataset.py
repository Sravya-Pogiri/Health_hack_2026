"""The loaded day: reference data plus every event file added so far."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Config, load_config, resolve_path
from .events import AppendReport, EventLog
from .loader import LoadReport, Reference, load_reference, read_csv_text, read_event_file
from .timeutil import ET, at_et, parse_hhmm


class Dataset:
    def __init__(self, cfg: Config | None = None, kit_dir: str | Path | None = None):
        self.cfg = cfg or load_config()
        self.kit_dir = Path(kit_dir) if kit_dir else resolve_path(self.cfg, "data.kit_dir")
        self.load_report = LoadReport()

        events_path = self.kit_dir / "event_stream.csv"
        columns, rows = read_event_file(events_path) if events_path.exists() else ([], [])
        self.ref: Reference = load_reference(self.kit_dir, self.cfg, rows, self.load_report)
        self.log = EventLog(self.cfg, set(self.ref.physicians), set(self.ref.hospitals))
        if rows or columns:
            self.kit_report = self.log.add_rows(columns, rows, source="event_stream.csv")
            self.load_report.files.append({"name": "event_stream.csv", "rows": len(rows),
                                           "status": "ok" if not self.kit_report.missing_columns else "missing_columns"})
        else:
            self.kit_report = AppendReport(source="event_stream.csv")
            self.load_report.files.append({"name": "event_stream.csv", "status": "missing", "rows": 0})
        self.appended_sources: list[str] = []

    @property
    def events(self):
        return self.log.events

    def append_csv_text(self, text: str, source: str) -> AppendReport:
        columns, rows = read_csv_text(text)
        report = self.log.add_rows(columns, rows, source=source)
        self.appended_sources.append(source)
        return report

    def append_file(self, path: str | Path) -> AppendReport:
        path = Path(path)
        return self.append_csv_text(path.read_text(encoding="utf-8-sig"), source=path.name)

    def default_now(self) -> datetime:
        """Kit default (config) before any append; the latest ingested_at after an append."""
        if self.appended_sources and self.cfg.get_path("clock.now_after_append") == "latest_ingested":
            latest = self.log.latest_ingested()
            if latest:
                return latest
        day = self.ref.scenario_date
        default = self.cfg.get_path("clock.default_now")
        if day and default:
            return at_et(day, parse_hhmm(default))
        latest = self.log.latest_ingested()
        return latest if latest else datetime.now(ET)
