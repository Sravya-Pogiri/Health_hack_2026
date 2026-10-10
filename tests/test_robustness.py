"""Robustness: bad rows never crash the engine, and nothing about the kit is hardcoded."""
import csv
import io
import shutil
from datetime import datetime, timedelta

import pytest

from conftest import KIT_DIR
from engine.dataset import Dataset
from engine.snapshot import CONSULT_COLUMNS, PHYSICIAN_COLUMNS, consult_rows, physician_rows
from engine.timeline import replay

HEADER = "event_id,event_time,ingested_at,consult_id,event_type,physician_id,hospital_id,description\n"


def test_bad_rows_are_skipped_and_reported(dataset):
    text = (
        HEADER
        + "EVT-99001,2026-03-17T18:21:00-04:00,2026-03-17T18:21:01-04:00,CON-2105,teleport_started,PHY-001,SYN-010,x\n"
        + "EVT-99002,not-a-time,2026-03-17T18:21:01-04:00,CON-2105,consult_triaged,,SYN-010,x\n"
        + ",2026-03-17T18:21:00-04:00,2026-03-17T18:21:01-04:00,CON-2105,consult_triaged,,SYN-010,x\n"
        + "EVT-99003,2026-03-17T18:21:00-04:00,2026-03-17T18:21:01-04:00,,camera_active,PHY-404,,Desktop agent: camera active\n"
        + "EVT-99004,2026-03-17T18:21:00-04:00,2026-03-17T18:21:01-04:00,,camera_active,,,missing physician\n"
        + "EVT-99005,2026-03-17T18:21:00-04:00,2026-03-17T18:21:01-04:00,CON-9999,consult_received,,SYN-999,"
          "Stroke consult request received\n"
    )
    report = dataset.append_csv_text(text, source="bad.csv")
    reasons = " | ".join(s["reason"] for s in report.skipped)
    assert "unknown event type" in reasons
    assert "malformed timestamp" in reasons
    assert "missing event_id" in reasons
    assert "without physician_id" in reasons
    assert report.unknown_physicians == ["PHY-404"]
    assert report.unknown_hospitals == ["SYN-999"]
    w = replay(dataset, dataset.default_now())   # must not crash
    assert "CON-9999" in w.consults


def test_missing_columns_rejected_cleanly(dataset):
    report = dataset.append_csv_text("event_id,event_time\nEVT-1,2026-03-17T18:00:00-04:00\n", source="short.csv")
    assert report.added == 0 and "ingested_at" in report.missing_columns


def test_missing_reference_rows_reported(tmp_path, cfg):
    kit = tmp_path / "kit"
    shutil.copytree(KIT_DIR, kit)
    lines = (kit / "baseline_hourly_volume.csv").read_text().splitlines()
    (kit / "baseline_hourly_volume.csv").write_text("\n".join(l for l in lines if not l.startswith("SYN-005,") or
                                                              ",18:00," not in l) + "\n")
    lines = (kit / "coverage_requirements.csv").read_text().splitlines()
    (kit / "coverage_requirements.csv").write_text("\n".join(l for l in lines if not l.startswith("SYN-012,")) + "\n")
    ds = Dataset(cfg, kit)
    text = " ".join(ds.load_report.warnings)
    assert "Baseline missing 1 hour(s) for SYN-005" in text
    assert "No coverage requirement for SYN-012" in text
    replay(ds, ds.default_now())


# ---------------------------------------------------------------- no hardcoding

SHIFT = timedelta(hours=3)


def _shift_iso(value):
    return (datetime.fromisoformat(value) + SHIFT).isoformat() if value else value


def _shift_hhmm(value, fmt="%H:%M"):
    t = datetime.strptime(value, fmt) + SHIFT
    return t.strftime(fmt)


def _rename(text, mapping):
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def _write(path, rows, columns):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns)
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue())


def _read(path):
    with open(path, encoding="utf-8-sig") as fh:
        r = csv.DictReader(fh)
        return list(r), r.fieldnames


@pytest.fixture()
def transformed_kit(tmp_path):
    """The kit with every ID renamed, every time moved 3 hours later, and one credential changed."""
    kit = tmp_path / "kit2"
    kit.mkdir()
    mapping = {f"PHY-{i:03d}": f"DOC-{100 + i}" for i in range(1, 15)}
    mapping.update({f"SYN-{i:03d}": f"HSP-{500 + i}" for i in range(1, 13)})
    mapping.update({f"CON-{n}": f"CASE-{n + 5000}" for n in list(range(1001, 1086)) + list(range(2101, 2119))})
    for f in KIT_DIR.glob("*.csv"):
        (kit / f.name).write_text(_rename(f.read_text(), dict(sorted(mapping.items(), key=lambda kv: -len(kv[0])))))

    rows, cols = _read(kit / "event_stream.csv")
    for r in rows:
        r["event_time"], r["ingested_at"] = _shift_iso(r["event_time"]), _shift_iso(r["ingested_at"])
    _write(kit / "event_stream.csv", rows, cols)

    rows, cols = _read(kit / "physicians_on_shift.csv")
    for r in rows:
        r["shift_start_et"], r["shift_end_et"] = _shift_hhmm(r["shift_start_et"]), _shift_hhmm(r["shift_end_et"])
    _write(kit / "physicians_on_shift.csv", rows, cols)

    rows, cols = _read(kit / "physicians_off_shift_roster.csv")
    for r in rows:
        r["last_shift_end"], r["next_shift_start"] = _shift_iso(r["last_shift_end"]), _shift_iso(r["next_shift_start"])
    _write(kit / "physicians_off_shift_roster.csv", rows, cols)

    rows, cols = _read(kit / "baseline_hourly_volume.csv")
    for r in rows:
        r["hour_start_et"] = _shift_hhmm(r["hour_start_et"])
    _write(kit / "baseline_hourly_volume.csv", rows, cols)

    rows, cols = _read(kit / "coverage_requirements.csv")
    for r in rows:
        a, b = r["time_block_et"].split("-")
        r["time_block_et"] = f"{_shift_hhmm(a)}-{_shift_hhmm(b)}"
    _write(kit / "coverage_requirements.csv", rows, cols)

    rows, cols = _read(kit / "credentialing_matrix.csv")
    rows[6][mapping["SYN-005"]] = "Y"   # credential Dr. Torres at Suncrest
    _write(kit / "credentialing_matrix.csv", rows, cols)

    for name, fields in (("snapshot_physicians_1820.csv", ("shift_et",)),
                         ("snapshot_open_consults_1820.csv", ("stage_entered_at",))):
        rows, cols = _read(kit / name)
        for r in rows:
            if "shift_et" in fields:
                a, b = r["shift_et"].split("-")
                r["shift_et"] = f"{_shift_hhmm(a)}-{_shift_hhmm(b)}"
            if "stage_entered_at" in fields:
                r["stage_entered_at"] = _shift_hhmm(r["stage_entered_at"], "%H:%M:%S")
        _write(kit / name, rows, cols)
    return kit, mapping


def test_engine_has_no_hardcoded_ids_or_times(transformed_kit, cfg):
    kit, mapping = transformed_kit
    ds = Dataset(cfg.with_overrides({"clock": {"default_now": "21:20:00"}}), kit)
    assert not ds.load_report.warnings
    w = replay(ds, ds.default_now())
    names = {h.id: h.name for h in ds.ref.hospitals.values()}

    exp_p, _ = _read(kit / "snapshot_physicians_1820.csv")
    got_p = {r["physician_id"]: r for r in physician_rows(w)}
    for r in exp_p:
        for col in PHYSICIAN_COLUMNS:
            assert got_p[r["physician_id"]][col] == r[col], (r["physician_id"], col)

    exp_c, _ = _read(kit / "snapshot_open_consults_1820.csv")
    got_c = {r["consult_id"]: r for r in consult_rows(w, names)}
    assert set(got_c) == {r["consult_id"] for r in exp_c}
    for r in exp_c:
        for col in CONSULT_COLUMNS:
            assert got_c[r["consult_id"]][col] == r[col], (r["consult_id"], col)

    # Dr. Torres now ends at 01:00 the next day: the shift crosses midnight.
    torres = w.physicians[mapping["PHY-007"]]
    assert torres.shift.end.day == torres.shift.start.day + 1
    assert mapping["SYN-005"] in ds.ref.credentials[mapping["PHY-007"]]
