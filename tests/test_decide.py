"""Milestone 2: detect and decide."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from engine.analysis import analyze
from engine.coverage import base_required
from engine.demand import surge_at
from engine.eligibility import evaluate, evaluate_physician
from engine.model import BaselineRow
from engine.routing import Candidate, next_up_all, route_core
from engine.timeline import replay
from engine.timeutil import ET

HEADER = "event_id,event_time,ingested_at,consult_id,event_type,physician_id,hospital_id,description\n"


@pytest.fixture()
def an(dataset, world):
    return analyze(dataset, world)


# ------------------------------------------------------------------ surge

def test_suncrest_surging_oak_valley_not(an):
    s = an.surges["SYN-005"]
    assert s.active and s.trailing_count == 5 and s.p90 == 1
    assert an.coverage_now["SYN-005"]["required"] == 2
    assert not an.surges["SYN-010"].active
    assert [h for h, s in an.surges.items() if s.active] == ["SYN-005"]


def test_recall_does_not_count_as_demand(dataset):
    dataset.append_csv_text(HEADER + "EVT-91001,2026-03-17T18:21:00-04:00,2026-03-17T18:21:01-04:00,CON-2105,"
                            "consult_received,,SYN-010,Stroke consult request received\n", "recall.csv")
    w = replay(dataset, dataset.default_now())
    assert surge_at(dataset.ref, w.consults, "SYN-010", w.now, dataset.cfg).trailing_count == 1


def test_two_hospitals_surge_and_new_stroke_at_surging_hospital(dataset):
    rows = [f"EVT-92{i:03d},2026-03-17T18:2{i}:00-04:00,2026-03-17T18:2{i}:02-04:00,CON-93{i:02d},consult_received,,"
            f"SYN-010,Stroke consult request received\n" for i in range(1, 3)]
    rows.append("EVT-92009,2026-03-17T18:23:30-04:00,2026-03-17T18:23:32-04:00,CON-9399,consult_received,,SYN-005,"
                "Stroke consult request received\n")
    dataset.append_csv_text(HEADER + "".join(rows), "surge.csv")
    w = replay(dataset, dataset.default_now())
    a = analyze(dataset, w)
    assert a.surges["SYN-010"].active and a.surges["SYN-010"].trailing_count == 3
    assert a.surges["SYN-005"].trailing_count == 6


def test_p90_of_zero_means_any_consult_is_a_surge(dataset, world):
    dataset.ref.baseline[("SYN-010", 18)] = BaselineRow("SYN-010", 18, 0.0, 0.0, 12)
    assert surge_at(dataset.ref, world.consults, "SYN-010", world.now, dataset.cfg).active


def test_trailing_window_spans_two_hours(dataset, world):
    # At 19:05 the window (18:05, 19:05] reaches back into the 18:00 hour but uses the 19:00 baseline.
    t = world.now.replace(hour=19, minute=5)
    s = surge_at(dataset.ref, world.consults, "SYN-005", t, dataset.cfg)
    assert s.hour == 19 and s.consult_ids == ["CON-2114"]   # CON-2107 (18:04:30) has dropped out of the window


def test_surge_hold_in_projection(an):
    assert an.surge_until["SYN-005"].strftime("%H:%M") == "20:00"


# ------------------------------------------------------------------ standards

def test_time_standards(an):
    status = {cid: chk.status for cid, chk in an.standards.items()}
    for cid in ("CON-2105", "CON-2110", "CON-2102", "CON-2112"):
        assert status[cid] == "critical", cid
    for cid in ("CON-2111", "CON-2115", "CON-2118"):
        assert status[cid] == "warning", cid
    assert an.standards["CON-2111"].started_at.strftime("%H:%M:%S") == "17:45:50"   # phone-only: from call end
    assert "CON-2106" not in an.standards      # general neurology imaging review has no standard
    assert an.standards["CON-2114"].minutes_to_critical == 8


def test_open_notes_near_shift_end(an):
    assert an.open_notes["PHY-003"].status == "critical"
    assert sorted(an.open_notes["PHY-003"].consult_ids) == ["CON-2111", "CON-2115"]
    assert an.open_notes["PHY-006"].status == "warning"
    assert "PHY-001" not in an.open_notes


# ------------------------------------------------------------------ routing

def test_next_up(an):
    assert an.next_up["SYN-010"].chosen is None
    assert an.next_up["SYN-005"].chosen is None
    assert an.next_up["SYN-011"].chosen is None
    assert an.next_up["SYN-001"].chosen == "PHY-004"


def test_routing_reproduces_every_recorded_failure(dataset):
    failures = [e for e in dataset.events if e.event_type == "routing_attempt_failed"]
    assert len(failures) == 4
    for ev in failures:
        w = replay(dataset, ev.event_time - timedelta(seconds=1))
        assert next_up_all(dataset, w)[ev.hospital_id].chosen is None, ev.event_id


def test_routing_never_picks_uncredentialed(dataset, world):
    for hid, result in next_up_all(dataset, world).items():
        if result.chosen:
            assert hid in dataset.ref.credentials[result.chosen]


def _cand(pid, consults, answered=None, night=False, creds=("H1",), blocker=None):
    return Candidate(pid, pid, set(creds), True, consults, answered, night, blocker)


def test_routing_rules_3_and_4(cfg):
    t = datetime(2026, 3, 17, 18, 0, tzinfo=ET)
    # Rule 4 ties go to whoever answered least recently; never answered counts as least recent.
    r = route_core([_cand("A", 9, t), _cand("B", 9, t - timedelta(minutes=30)), _cand("C", 10)], "H1", cfg)
    assert r.chosen == "B"
    r = route_core([_cand("A", 9, t), _cand("B", 9)], "H1", cfg)
    assert r.chosen == "B"
    # Rule 3: low volume first (6 or fewer on days, 4 or fewer on nights).
    r = route_core([_cand("D", 6, t), _cand("N", 5, t, night=True), _cand("X", 2, t, blocker="busy")], "H1", cfg)
    assert r.chosen == "D"
    # Rule 2 and rule 1.
    r = route_core([_cand("A", 1, blocker="On a call."), _cand("B", 0, creds=("H2",))], "H1", cfg)
    assert r.chosen is None


# ------------------------------------------------------------------ coverage

def test_staffed_and_free_now(an):
    cov = an.coverage_now
    assert cov["SYN-005"]["staffed"] == ["PHY-003", "PHY-005", "PHY-006"] and cov["SYN-005"]["free"] == []
    assert cov["SYN-010"]["staffed"] == ["PHY-002"] and cov["SYN-010"]["unavailable"] == ["PHY-008"]
    assert cov["SYN-010"]["free"] == []


def test_projected_gaps(an):
    gaps = {(g.hospital_id, g.start.strftime("%H:%M"), g.end.strftime("%H:%M"), g.required, g.staffed) for g in an.gaps}
    assert gaps == {("SYN-008", "20:00", "22:00", 2, 1), ("SYN-001", "21:00", "22:00", 2, 1),
                    ("SYN-003", "21:00", "22:00", 2, 1)}
    northfield = next(g for g in an.gaps if g.hospital_id == "SYN-008")
    assert "shift_end" in northfield.causes


def test_projection_covers_at_least_90_minutes_and_scheduled_starts(an, dataset):
    times = [datetime.fromisoformat(t) for t in an.projection["times"]]
    assert times[-1] - times[0] >= timedelta(minutes=90)
    at_1915 = next(p for p in an.projection["hospitals"]["SYN-005"] if p["t"].endswith("19:15:00-04:00"))
    assert "PHY-011" in at_1915["staffed_ids"] and at_1915["required"] == 2


def test_overnight_block_wraps_midnight(dataset, world):
    late = world.now.replace(hour=23)
    early = (world.now + timedelta(days=1)).replace(hour=3)
    assert base_required(dataset, "SYN-001", late) == 1
    assert base_required(dataset, "SYN-001", early) == 1
    assert base_required(dataset, "SYN-001", world.now) == 2


def test_now_gaps(an):
    assert {g.hospital_id for g in an.now_gaps} == {"SYN-005", "SYN-010", "SYN-011"}


# ------------------------------------------------------------------ eligibility

def test_only_okafor_eligible_for_the_current_gap(an):
    rows = {r.physician_id: r for r in an.cluster.rows}
    assert [r.physician_id for r in an.cluster.eligible] == ["PHY-009"]
    first_fail = {pid: next(x.key for x in r.rules if not x.passed) for pid, r in rows.items() if not r.eligible}
    assert first_fail == {"PHY-010": "credentialed", "PHY-011": "no_overlap", "PHY-012": "opt_in",
                          "PHY-013": "opt_in", "PHY-014": "week_hours"}
    offer = rows["PHY-009"].offer
    assert offer.start.strftime("%H:%M") == "18:35" and offer.end.strftime("%H:%M") == "22:00"
    assert set(offer.hospitals) == {"SYN-005", "SYN-008", "SYN-010"}   # combined coverage includes Northfield at 8 PM
    assert offer.effective_minutes >= 120


def test_nobody_qualifies_means_escalate(an):
    cedar = next(g for g in an.gaps if g.hospital_id == "SYN-001")
    assert not an.gap_eligibility[cedar.id].eligible
    assert an.gap_plans[cedar.id].primary.kind == "escalate_lead"
    northfield = next(g for g in an.gaps if g.hospital_id == "SYN-008")
    assert an.gap_plans[northfield.id].primary.kind == "covered_by_offer"


def _okafor_variant(dataset, **changes):
    return replace(dataset.ref.physicians["PHY-009"], **changes)


def test_boundaries_exactly_10_hours_rest_and_60_hours(dataset, world, an):
    gap = next(g for g in an.now_gaps if g.hospital_id == "SYN-010")
    creds = dataset.ref.credentials["PHY-009"]
    names = {h.id: h.name for h in dataset.ref.hospitals.values()}
    start = world.now + timedelta(minutes=15)
    exact_rest = _okafor_variant(dataset, last_shift_end=start - timedelta(hours=10))
    row = evaluate_physician(exact_rest, creds, [gap], [gap], world.now, dataset.cfg, {}, names)
    assert next(r for r in row.rules if r.key == "rest").passed
    short_rest = _okafor_variant(dataset, last_shift_end=start - timedelta(hours=10) + timedelta(seconds=60))
    row = evaluate_physician(short_rest, creds, [gap], [gap], world.now, dataset.cfg, {}, names)
    assert not next(r for r in row.rules if r.key == "rest").passed
    # 60 hours exactly passes: 57.75 h worked + a 2 h 15 min offer = 60.
    exact_week = _okafor_variant(dataset, hours_worked_last_7d=57.75)
    row = evaluate_physician(exact_week, creds, [gap], [gap], world.now, dataset.cfg, {}, names)
    assert next(r for r in row.rules if r.key == "week_hours").passed and row.eligible
    over = _okafor_variant(dataset, hours_worked_last_7d=57.8)
    row = evaluate_physician(over, creds, [gap], [gap], world.now, dataset.cfg, {}, names)
    assert not row.eligible


def test_rest_is_measured_at_offer_start(dataset, world, an):
    """Fails for an offer starting at 6:35 PM, passes for the 8:00 PM Northfield gap."""
    creds = dataset.ref.credentials["PHY-009"]
    names = {h.id: h.name for h in dataset.ref.hospitals.values()}
    tired = _okafor_variant(dataset, last_shift_end=world.now.replace(hour=9, minute=0))
    now_gap = next(g for g in an.now_gaps if g.hospital_id == "SYN-010")
    later_gap = next(g for g in an.gaps if g.hospital_id == "SYN-008")
    early = evaluate_physician(tired, creds, [now_gap], [now_gap], world.now, dataset.cfg, {}, names)
    late = evaluate_physician(tired, creds, [later_gap], [later_gap], world.now, dataset.cfg, {}, names)
    assert not next(r for r in early.rules if r.key == "rest").passed
    assert next(r for r in late.rules if r.key == "rest").passed


def test_weekly_offer_cap_and_open_offer(dataset, world, an):
    hist = {"PHY-009": {"offers_this_week": 3}}
    res = evaluate(dataset, world, an.now_gaps, an.all_gaps, hist)
    assert not res.eligible
    res = evaluate(dataset, world, an.now_gaps, an.all_gaps, {"PHY-009": {"open_offer": "off-0001"}})
    assert not res.eligible


# ------------------------------------------------------------------ issues and plans

def test_ranked_queue(an):
    top = [g.key for g in an.groups[:5]]
    assert top[:2] == ["SYN-011", "SYN-010"]          # both stroke, critical, nobody working on them; Harbor waited longer
    assert set(top[2:5]) == {"SYN-005", "SYN-009", "phy-PHY-003"}
    for issue in an.issues:
        assert issue.what and issue.why and issue.plan is not None, issue.id


def test_plans(an):
    by_id = {i.id: i for i in an.issues}
    oak = by_id["iss-unassigned-CON-2105"].plan
    assert oak.primary.kind == "split_shift" and oak.primary.physician_id == "PHY-009"
    assert oak.primary.approver == "clinical_lead"
    assert any(o.kind == "escalate_lead" for o in oak.also_now)
    assert {o.kind for o in oak.alternatives} >= {"reassign", "interrupt", "start_early"}
    harbor = by_id["iss-assigned-unavailable-CON-2110"].plan
    assert harbor.primary.kind == "start_early" and harbor.primary.physician_id == "PHY-011"
    assert any(o.kind == "verify" and o.physician_id == "PHY-001" for o in harbor.also_now)
    assert by_id["iss-hospital-delay-CON-2102"].plan.primary.kind == "escalate_hospital"
    assert by_id["iss-breach-CON-2112"].plan.primary.kind == "wait"


def test_recall_moves_consult_up(dataset):
    dataset.append_csv_text(HEADER + "EVT-91101,2026-03-17T18:21:00-04:00,2026-03-17T18:21:01-04:00,CON-2105,"
                            "consult_received,,SYN-010,Stroke consult request received\n", "recall.csv")
    w = replay(dataset, dataset.default_now())
    a = analyze(dataset, w)
    assert a.groups[0].key == "SYN-010"
    assert "called again" in a.groups[0].top.why
