"""Event handling: duplicates, late events, ordering, re-calls, appends."""
from datetime import timedelta

from engine.consults import ConsultState, apply_consult_event
from engine.timeline import replay

HEADER = "event_id,event_time,ingested_at,consult_id,event_type,physician_id,hospital_id,description\n"


def test_duplicate_event_counted_once(dataset):
    ids = [e.event_id for e in dataset.events]
    assert ids.count("EVT-00529") == 1
    assert len(dataset.events) == 1163
    dup = dataset.kit_report.duplicates
    assert len(dup) == 1 and dup[0]["event_id"] == "EVT-00529"


def test_late_event_placed_by_event_time(dataset, world):
    late = {e["event_id"] for e in dataset.kit_report.late_events}
    assert "EVT-01161" in late
    con = world.consults["CON-2107"]
    assert con.stage == "Video Consult In Progress"
    assert con.stage_entered_at.strftime("%H:%M:%S") == "18:11:20"


def test_arrival_order_replay_gives_wrong_answer(dataset, world):
    """Replaying in arrival order (using arrival time as the time) puts CON-2107's video at 18:18:05."""
    naive: dict[str, ConsultState] = {}
    for ev in sorted(dataset.events, key=lambda e: (e.ingested_at, e.seq)):
        shifted = type(ev)(**{**ev.__dict__, "event_time": ev.ingested_at})
        apply_consult_event(naive, shifted, {})
    assert naive["CON-2107"].stage_entered_at.strftime("%H:%M:%S") == "18:18:05"
    assert world.consults["CON-2107"].stage_entered_at != naive["CON-2107"].stage_entered_at


def test_event_not_counted_before_it_arrives(dataset):
    """At 18:15 the late video start has happened but not arrived, so the camera looks like an outside call."""
    t = dataset.default_now() - timedelta(minutes=5)
    w = replay(dataset, t)
    assert w.consults["CON-2107"].stage == "Provider Contacted"
    assert w.physicians["PHY-006"].derived_state == "Camera Active - No Synapse Session"


def test_recall_is_same_patient(dataset):
    before_world = replay(dataset, dataset.default_now())
    before = before_world.consults["CON-2105"]
    report = dataset.append_csv_text(
        HEADER + "EVT-90001,2026-03-17T18:21:00-04:00,2026-03-17T18:21:02-04:00,CON-2105,consult_received,,SYN-010,"
                 "Stroke consult request received\n", source="recall.csv")
    assert report.recalls and report.recalls[0]["consult_id"] == "CON-2105"
    assert report.new_consults == []
    w = replay(dataset, dataset.default_now())
    con = w.consults["CON-2105"]
    assert con.recall_count == 1
    assert con.received_at == before.received_at          # clock not restarted
    assert con.stage == "Triaged" and con.stage_entered_at == before.stage_entered_at
    assert len(w.consults) == len(before_world.consults)   # no new consult, so no new demand


def test_different_consults_same_hospital_stay_separate(dataset):
    dataset.append_csv_text(
        HEADER
        + "EVT-90011,2026-03-17T18:21:00-04:00,2026-03-17T18:21:02-04:00,CON-9011,consult_received,,SYN-005,"
          "Stroke consult request received\n"
        + "EVT-90012,2026-03-17T18:21:30-04:00,2026-03-17T18:21:32-04:00,CON-9012,consult_received,,SYN-005,"
          "Stroke consult request received\n",
        source="two_new.csv")
    w = replay(dataset, dataset.default_now())
    assert "CON-9011" in w.consults and "CON-9012" in w.consults
    assert w.consults["CON-9011"].recall_count == 0 and w.consults["CON-9012"].recall_count == 0


def test_explicit_repeat_reference_links_to_original(dataset):
    report = dataset.append_csv_text(
        HEADER + "EVT-90021,2026-03-17T18:22:00-04:00,2026-03-17T18:22:01-04:00,CON-9021,consult_received,,SYN-010,"
                 "Stroke consult request received; hospital calling again about CON-2105\n",
        source="explicit.csv")
    assert report.recalls[0]["consult_id"] == "CON-2105"
    w = replay(dataset, dataset.default_now())
    assert "CON-9021" not in w.consults
    assert w.consults["CON-2105"].recall_count == 1


def test_append_without_restart(dataset):
    before_now = dataset.default_now()
    text = (
        HEADER
        # new stroke consult at the surging hospital
        + "EVT-90031,2026-03-17T18:25:00-04:00,2026-03-17T18:25:02-04:00,CON-9031,consult_received,,SYN-005,"
          "Stroke consult request received\n"
        + "EVT-90032,2026-03-17T18:26:00-04:00,2026-03-17T18:26:03-04:00,CON-9031,consult_triaged,,SYN-005,"
          "Consult triaged\n"
        # duplicate inside the upload
        + "EVT-90032,2026-03-17T18:26:00-04:00,2026-03-17T18:26:05-04:00,CON-9031,consult_triaged,,SYN-005,"
          "Consult triaged\n"
        # duplicate of an existing kit event
        + "EVT-01163,2026-03-17T18:19:40-04:00,2026-03-17T18:19:43-04:00,CON-2109,incoming_call_ringing,PHY-007,"
          "SYN-012,Hospital call ringing to intended physician; not yet answered\n"
        # same content as a kit event under a new id
        + "EVT-90033,2026-03-17T18:19:10-04:00,2026-03-17T18:19:14-04:00,CON-2105,routing_attempt_failed,,SYN-010,"
          "No available credentialed provider; retry scheduled\n"
    )
    report = dataset.append_csv_text(text, source="live.csv")
    assert report.added == 2
    assert len(report.duplicates) == 3
    assert report.new_consults == ["CON-9031"]
    now = dataset.default_now()
    assert now > before_now and now.strftime("%H:%M:%S") == "18:26:03"
    w = replay(dataset, now)
    assert w.consults["CON-9031"].stage == "Triaged"
    assert len(w.consults["CON-2105"].routing_failures) == 3


def test_events_after_now_are_held(dataset):
    dataset.append_csv_text(
        HEADER + "EVT-90041,2026-03-17T19:30:00-04:00,2026-03-17T19:30:01-04:00,CON-9041,consult_received,,SYN-001,"
                 "Stroke consult request received\n", source="future.csv")
    w = replay(dataset, dataset.default_now() - timedelta(minutes=60))
    assert "CON-9041" not in w.consults
    assert w.events_pending >= 1
