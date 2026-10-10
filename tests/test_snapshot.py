"""Blocking test: replaying the kit to 18:20 must reproduce both reference snapshots exactly."""
import csv

from conftest import KIT_DIR
from engine.snapshot import CONSULT_COLUMNS, PHYSICIAN_COLUMNS, consult_rows, physician_rows


def _read(name):
    with open(KIT_DIR / name, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _diff(expected, actual, key, columns):
    exp = {r[key]: r for r in expected}
    act = {r[key]: r for r in actual}
    problems = []
    for k in sorted(set(exp) | set(act)):
        if k not in act:
            problems.append(f"{k}: missing from replay")
            continue
        if k not in exp:
            problems.append(f"{k}: in replay but not in snapshot")
            continue
        for col in columns:
            if exp[k][col] != act[k][col]:
                problems.append(f"{k}.{col}: expected {exp[k][col]!r}, got {act[k][col]!r}")
    return problems


def test_now_is_1820(world):
    assert world.now.strftime("%Y-%m-%d %H:%M:%S %z") == "2026-03-17 18:20:00 -0400"


def test_physician_snapshot_matches(world):
    problems = _diff(_read("snapshot_physicians_1820.csv"), physician_rows(world), "physician_id", PHYSICIAN_COLUMNS)
    assert not problems, "Physician snapshot mismatch:\n" + "\n".join(problems)


def test_open_consult_snapshot_matches(world, dataset):
    names = {h.id: h.name for h in dataset.ref.hospitals.values()}
    problems = _diff(_read("snapshot_open_consults_1820.csv"), consult_rows(world, names), "consult_id", CONSULT_COLUMNS)
    assert not problems, "Open consult snapshot mismatch:\n" + "\n".join(problems)
