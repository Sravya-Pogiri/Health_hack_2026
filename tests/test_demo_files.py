"""The live-test practice files: each one, from the kit state and in sequence, does what its description says."""
from pathlib import Path

import pytest

from engine.notify import Notifier
from engine.service import RelayService
from engine.storage import LocalStorage

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo_events"


@pytest.fixture()
def svc(tmp_path, cfg):
    return RelayService(cfg, LocalStorage(tmp_path / "s"), Notifier())


def load(svc, name):
    return svc.upload((DEMO / name).read_text(), name)


def test_every_file_loads_in_sequence(svc):
    for f in sorted(DEMO.glob("*.csv")):
        r = load(svc, f.name)
        assert r["ok"], f.name
        svc.state()


def test_01_new_stroke_at_surging_hospital(svc):
    r = load(svc, "01_new_stroke_suncrest.csv")
    assert r["new_consults"] == ["CON-2120"]
    assert any(i["id"] == "iss-unassigned-CON-2120" for i in r["new_issues"])
    assert svc.analysis().surges["SYN-005"].trailing_count == 6


def test_02_recall_moves_to_top(svc):
    r = load(svc, "02_recall_oak_valley.csv")
    assert r["recalls"] and r["new_consults"] == []
    assert svc.analysis().groups[0].key == "SYN-010"


def test_03_williams_back_resolves_harbor(svc):
    r = load(svc, "03_williams_back.csv")
    assert "iss-assigned-unavailable-CON-2110" in {i["id"] for i in r["resolved_issues"]}


def test_04_late_and_duplicates(svc):
    r = load(svc, "04_late_and_duplicate.csv")
    assert r["added"] == 3 and r["duplicates_skipped"] == 3 and len(r["late_events"]) == 3
    assert not svc.analysis().world.consults["CON-2108"].is_open


def test_05_and_06_joiners(svc):
    load(svc, "05_okafor_online.csv")
    assert svc.analysis().world.physicians["PHY-009"].on_shift
    load(svc, "06_cho_starts.csv")
    an = svc.analysis()
    assert an.world.physicians["PHY-011"].on_shift and not an.world.physicians["PHY-003"].on_shift


def test_07_quiet_feed(svc):
    load(svc, "07_quiet_feed.csv")
    svc.clock_cmd("advance", minutes=20)
    s = svc.state()
    assert s["data_health"]["stale"] and s["issues"][0]["issues"][0]["type"] == "data_stale"


def test_08_unexpected(svc):
    r = load(svc, "08_unexpected.csv")
    reasons = " ".join(x["reason"] for x in r["skipped"])
    assert "unknown event type" in reasons and "malformed timestamp" in reasons
    assert r["unknown_physicians"] == ["PHY-099"] and r["unknown_hospitals"] == ["SYN-013"]
    an = svc.analysis()
    assert an.world.consults["CON-3001"].missing_received
    assert an.surges["SYN-012"].active
