"""API: the full loop over HTTP, live event upload without a restart, clock, reset, and safety checks."""
import pytest
from fastapi.testclient import TestClient

import api.main as main
from engine.notify import Notifier
from engine.service import RelayService
from engine.storage import LocalStorage

HEADER = "event_id,event_time,ingested_at,consult_id,event_type,physician_id,hospital_id,description\n"


@pytest.fixture()
def client(tmp_path, cfg, monkeypatch):
    monkeypatch.setenv("RELAY_TICKER", "0")
    monkeypatch.delenv("RELAY_SHARED_SECRET", raising=False)
    main.set_service(RelayService(cfg, LocalStorage(tmp_path / "state"), Notifier()))
    with TestClient(main.app) as c:
        yield c


def test_state(client):
    s = client.get("/api/state").json()
    assert s["now_label"] == "6:20 PM ET"
    assert s["issues"][0]["severity"] == "critical"
    assert {h["id"] for h in s["hospitals"] if h["promise_at_risk"]} >= {"SYN-005", "SYN-010", "SYN-011"}
    assert client.get("/api/health").json()["ok"]


def test_full_loop_over_http(client):
    plan = client.post("/api/offers/propose", json={"issue_id": "iss-unassigned-CON-2105"}).json()
    assert plan["status"] == "pending_approval" and plan["eligibility"]["recommended"] == "PHY-009"
    assert client.get("/api/state").json()["pending_approvals"] == [plan["id"]]
    plan = client.post(f"/api/offers/{plan['id']}/approve", json={"by": "Dr. Lead"}).json()
    offer = plan["offers"][0]
    phone = client.get(f"/api/offers/{offer['id']}").json()
    assert phone["status"] == "sent" and phone["seconds_left"] == 600
    assert "CON-" not in phone["message"]
    assert client.post(f"/api/offers/{offer['id']}/accept").json()["ok"]
    again = client.post(f"/api/offers/{offer['id']}/accept").json()
    assert not again["ok"]
    s = client.get("/api/state").json()
    assert s["joins"][0]["physician_id"] == "PHY-009"
    assert not [g for g in s["gaps"] if g["hospital_id"] == "SYN-008"]      # Northfield's 8 PM gap is covered
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert actions[:4] == ["proposed", "approved", "sent", "accepted"]


def test_start_early_request(client):
    plan = client.post("/api/offers/propose", json={"issue_id": "iss-assigned-unavailable-CON-2110"}).json()
    assert plan["kind"] == "start_early" and plan["candidates"][0]["physician_id"] == "PHY-011"


def test_nothing_to_propose_when_fix_is_not_an_offer(client):
    r = client.post("/api/offers/propose", json={"issue_id": "iss-hospital-delay-CON-2102"})
    assert r.status_code == 409


def test_upload_new_events_without_restart(client):
    text = (HEADER
            + "EVT-95001,2026-03-17T18:24:00-04:00,2026-03-17T18:24:02-04:00,CON-9501,consult_received,,SYN-005,"
              "Stroke consult request received\n"
            + "EVT-95002,2026-03-17T18:25:00-04:00,2026-03-17T18:25:03-04:00,CON-9501,consult_triaged,,SYN-005,"
              "Consult triaged\n"
            + "EVT-01163,2026-03-17T18:19:40-04:00,2026-03-17T18:19:43-04:00,CON-2109,incoming_call_ringing,PHY-007,"
              "SYN-012,Hospital call ringing to intended physician; not yet answered\n")
    r = client.post("/api/events/upload", files={"file": ("live.csv", text, "text/csv")}).json()
    assert r["ok"] and r["added"] == 2 and r["duplicates_skipped"] == 1
    assert r["now"].startswith("2026-03-17T18:25:03")
    assert any(i["id"] == "iss-unassigned-CON-9501" for i in r["new_issues"])
    assert r["top_issue"]["what"]
    s = client.get("/api/state").json()
    assert any(c["id"] == "CON-9501" for c in s["consults"])
    suncrest = next(h for h in s["hospitals"] if h["id"] == "SYN-005")
    assert suncrest["surge"]["trailing_count"] == 6


def test_upload_rejects_wrong_columns(client):
    r = client.post("/api/events/upload", files={"file": ("bad.csv", "a,b\n1,2\n", "text/csv")}).json()
    assert not r["ok"] and "event_id" in r["missing_columns"]


def test_single_event(client):
    r = client.post("/api/events", json={"event_type": "physician_status_changed", "physician_id": "PHY-008",
                                         "description": "Self-reported status: Available",
                                         "event_time": "2026-03-17T18:22:00-04:00"}).json()
    assert r["ok"] and r["added"] == 1
    s = client.get("/api/state").json()
    williams = next(p for p in s["physicians"] if p["id"] == "PHY-008")
    assert williams["self_reported_status"] == "Available"


def test_clock_drives_offer_expiry(client):
    plan = client.post("/api/offers/propose", json={"issue_id": "iss-unassigned-CON-2105"}).json()
    offer = client.post(f"/api/offers/{plan['id']}/approve", json={}).json()["offers"][0]
    client.post("/api/clock", json={"action": "advance", "minutes": 10})
    assert client.get(f"/api/offers/{offer['id']}").json()["status"] == "expired"
    late = client.post(f"/api/offers/{offer['id']}/accept").json()
    assert not late["ok"] and late["code"] == "expired"
    s = client.get("/api/state").json()
    assert s["clock"]["mode"] == "manual" and s["now"].startswith("2026-03-17T18:30")


def test_reset(client):
    client.post("/api/clock", json={"action": "advance", "minutes": 30})
    client.post("/api/events", json={"event_type": "consult_received", "consult_id": "CON-9601",
                                     "hospital_id": "SYN-001", "description": "Stroke consult request received"})
    client.post("/api/reset")
    s = client.get("/api/state").json()
    assert s["now_label"] == "6:20 PM ET" and not any(c["id"] == "CON-9601" for c in s["consults"])


def test_internal_endpoints_need_the_secret(client, monkeypatch):
    monkeypatch.setenv("RELAY_SHARED_SECRET", "s3cret")
    assert client.post("/api/timers/tick").status_code == 401
    assert client.post("/api/timers/tick", headers={"X-Relay-Secret": "s3cret"}).status_code == 200
    assert client.post("/api/internal/reload", headers={"X-Relay-Secret": "s3cret"}).json()["ok"]


def test_flood_guard_proof(client):
    r = client.get("/api/simulate/flood-guard").json()
    okafor = next(j for j in r["joiners"] if j["physician_id"] == "PHY-009")
    gap = set(okafor["gap_hospitals"])
    assert set(okafor["without"]["by_hospital"]) - gap        # without the guard: calls outside the gap hospitals
    assert set(okafor["with"]["by_hospital"]) <= gap          # with the guard: only the gap hospitals
    assert okafor["with"]["calls_outside_gap_hospitals"] == 0
    assert r["headline"] and r["runs"] == 50


def test_static_route_does_not_escape(client):
    assert client.get("/..%2F..%2Fconfig.yaml").status_code == 404 or \
        "min_rest_hours" not in client.get("/..%2F..%2Fconfig.yaml").text


def test_standouts(client):
    s = client.get("/api/scoreboard").json()
    assert any("Suncrest" in h for h in s["headlines"]) and any("Okafor" in h for h in s["headlines"])
    ins = client.get("/api/insights").json()
    assert {h["consult_id"] for h in ins["handoffs"]} >= {"CON-2111", "CON-2115", "CON-2118"}
    assert ins["credentialing"]["top"][0]["hospital_id"] == "SYN-010"
    assert "credentialing" in ins["credentialing"]["note"]
    assert client.post("/api/handoffs/CON-2102/confirm", json={}).json()["kind"] == "handoff_confirmed"
    assert client.get("/api/state").json()["handoff_confirmations"]["CON-2102"]
    sim = client.get("/api/simulate/flood-guard").json()
    assert sim["value_of_acting"]["physician_hours"] > 0
    plan = client.post("/api/offers/propose", json={"issue_id": "iss-unassigned-CON-2105"}).json()
    summary = next(p for p in client.get("/api/state").json()["plans"] if p["id"] == plan["id"])["summary"]
    assert summary["source"] == "template" and "Oak Valley Medical" in summary["text"]
    offer = client.post(f"/api/offers/{plan['id']}/approve", json={}).json()["offers"][0]
    assert client.get(f"/api/offers/{offer['id']}/voice").status_code == 404     # no ElevenLabs key: browser voice

