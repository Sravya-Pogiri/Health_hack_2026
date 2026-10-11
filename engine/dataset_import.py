"""Import a whole new dataset (the seven kit files) uploaded as CSV files or a zip.

Each file is matched to its role by name first ("Hospitals.csv", "credentialing matrix.csv") and, failing
that, by its columns, so renamed files still load. Nothing is written unless every required file is there.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .loader import EVENT_COLUMNS, REQUIRED_COLUMNS, read_csv_text

# Canonical file name -> words that identify it in an uploaded file name.
NAME_HINTS = {
    "physicians_off_shift_roster.csv": ["off_shift", "offshift", "roster"],
    "physicians_on_shift.csv": ["on_shift", "onshift"],
    "credentialing_matrix.csv": ["credential"],
    "baseline_hourly_volume.csv": ["baseline", "hourly_volume", "volume"],
    "coverage_requirements.csv": ["coverage", "requirement"],
    "event_stream.csv": ["event"],
    "hospitals.csv": ["hospital"],
}

REQUIRED = ["hospitals.csv", "physicians_on_shift.csv", "credentialing_matrix.csv",
            "coverage_requirements.csv", "event_stream.csv"]
OPTIONAL = ["physicians_off_shift_roster.csv", "baseline_hourly_volume.csv"]

LABELS = {
    "hospitals.csv": "Hospitals",
    "physicians_on_shift.csv": "Doctors on shift",
    "physicians_off_shift_roster.csv": "Off-shift roster",
    "credentialing_matrix.csv": "Credentialing",
    "baseline_hourly_volume.csv": "Normal hourly volume",
    "coverage_requirements.csv": "Coverage requirements",
    "event_stream.csv": "Event stream",
}


def _norm(name: str) -> str:
    return Path(name).stem.lower().replace(" ", "_").replace("-", "_")


def _by_name(name: str) -> str | None:
    n = _norm(name)
    for role, hints in NAME_HINTS.items():   # order matters: "off_shift" before "on_shift", "event" before "hospital"
        if any(h in n for h in hints):
            return role
    return None


def _looks_like_matrix(columns: list[str], rows: list[dict]) -> bool:
    """A credentialing matrix: physician_id, maybe name, then one Y/N column per hospital."""
    flags = [c for c in columns if c not in ("physician_id", "name")]
    values = {str(r.get(c, "")).strip().lower() for r in rows[:20] for c in flags}
    return bool(flags) and values <= {"y", "n", "yes", "no", "true", "false", "1", "0", ""}


def _by_columns(columns: list[str], rows: list[dict]) -> str | None:
    cols = set(columns)
    if set(EVENT_COLUMNS) <= cols:
        return "event_stream.csv"
    for role in ("physicians_off_shift_roster.csv", "baseline_hourly_volume.csv", "coverage_requirements.csv",
                 "physicians_on_shift.csv", "hospitals.csv"):
        if set(REQUIRED_COLUMNS[role]) <= cols:
            return role
    if "physician_id" in cols and _looks_like_matrix(columns, rows):
        return "credentialing_matrix.csv"
    return None


def expand(files: list[tuple[str, bytes]]) -> list[tuple[str, str]]:
    """Unpack zips; return (name, text) for every CSV."""
    out: list[tuple[str, str]] = []
    for name, data in files:
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    base = Path(info.filename).name
                    if info.is_dir() or not base.lower().endswith(".csv") or "__MACOSX" in info.filename or base.startswith("."):
                        continue
                    out.append((base, z.read(info).decode("utf-8-sig")))
        elif name.lower().endswith(".csv"):
            out.append((Path(name).name, data.decode("utf-8-sig")))
    return out


def classify(files: list[tuple[str, str]]) -> dict:
    """Match uploaded files to roles. Returns {"roles": {role: (name, text)}, "files": [...], "errors": [...]}."""
    roles: dict[str, tuple[str, str]] = {}
    report: list[dict] = []
    errors: list[str] = []
    for name, text in files:
        columns, rows = read_csv_text(text)
        if "snapshot" in _norm(name):                  # reference snapshots are for checking, not loading
            report.append({"name": name, "rows": len(rows), "role": None, "label": "Snapshot (not needed)", "status": "ignored"})
            continue
        role = _by_name(name)
        if role and not set(REQUIRED_COLUMNS[role]) <= set(columns):
            role = _by_columns(columns, rows) or role  # the name was misleading; trust the columns
        role = role or _by_columns(columns, rows)
        entry = {"name": name, "rows": len(rows), "role": role, "label": LABELS.get(role, "Not recognised")}
        if role is None:
            entry["status"] = "ignored"
        elif role in roles:
            entry["status"] = "duplicate"
            errors.append(f"Two files look like {LABELS[role]}: {roles[role][0]} and {name}.")
        else:
            missing = [c for c in REQUIRED_COLUMNS[role] if c not in columns]
            if missing:
                entry["status"] = "missing_columns"
                entry["missing_columns"] = missing
                errors.append(f"{name} ({LABELS[role]}) is missing columns: {', '.join(missing)}.")
            else:
                entry["status"] = "ok"
                roles[role] = (name, text)
        report.append(entry)
    missing_roles = [r for r in REQUIRED if r not in roles]
    for r in missing_roles:
        errors.append(f"No {LABELS[r]} file ({r}).")
    warnings = [f"No {LABELS[r]} file ({r}); features that need it are limited." for r in OPTIONAL if r not in roles]
    return {"roles": roles, "files": report, "errors": errors, "warnings": warnings,
            "missing": missing_roles}


def write(roles: dict[str, tuple[str, str]], root: Path) -> Path:
    """Save the files under their canonical names in a new folder and return it."""
    folder = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    folder.mkdir(parents=True, exist_ok=True)
    for role, (_, text) in roles.items():
        (folder / role).write_text(text, encoding="utf-8")
    return folder
