"""Act: offer workflow edge cases, privacy guard, and audit."""
from datetime import datetime, timedelta

import pytest

from engine.notify import Notifier
from engine.offers import Candidate, OfferBook
from engine.privacy import PrivacyViolation, build_offer_message, check_message
from engine.timeutil import ET

T0 = datetime(2026, 3, 17, 18, 20, tzinfo=ET)
NAMES = {"H1": "Suncrest Medical Pavilion", "H2": "Oak Valley Medical"}


def cand(pid, rank, online=15):
    start = T0 + timedelta(minutes=online)
    return Candidate(pid, f"Dr. {pid}", rank, start, start + timedelta(hours=3), start, online, ["H1", "H2"],
                     "app_push", ["credentialed", "rested"])


@pytest.fixture()
def book(cfg):
    b = OfferBook(cfg, Notifier(), None, NAMES)
    b.consult_ids = {"CON-2105", "CON-2114"}
    b.descriptions = {"Stroke consult request received", "Hospital calls assigned physician"}
    return b


def propose(book, *pids, slots=1, mode="one_at_a_time"):
    return book.propose(T0, "coordinator", "split_shift", "Split shift: test", ["now-H1"], ["H1", "H2"],
                        "coverage gap now", [cand(p, i + 1) for i, p in enumerate(pids)], {"rows": []}, slots, mode)


def test_nothing_is_sent_without_approval(book):
    plan = propose(book, "A")
    assert plan.status == "pending_approval" and not book.offers
    assert book.audit[0]["action"] == "proposed" and book.audit[0]["eligibility"] == {"rows": []}
    book.reject(plan.id, T0, "clinical_lead", "Not needed")
    assert plan.status == "rejected" and not book.offers
    with pytest.raises(ValueError):
        book.approve(plan.id, T0, "clinical_lead")


def test_offers_expire_after_10_minutes_and_move_down_the_list(book):
    plan = propose(book, "A", "B")
    book.approve(plan.id, T0, "clinical_lead")
    first = book.offers[plan.offer_ids[0]]
    assert first.physician_id == "A" and first.expires_at == T0 + timedelta(minutes=10)
    book.tick(T0 + timedelta(minutes=9, seconds=59), {"now-H1"}, {})
    assert first.status == "sent"
    book.tick(T0 + timedelta(minutes=10), {"now-H1"}, {})
    assert first.status == "expired"
    second = book.offers[plan.offer_ids[1]]
    assert second.physician_id == "B" and second.status == "sent"
    late = book.accept(first.id, T0 + timedelta(minutes=11))
    assert not late["ok"] and late["code"] == "expired"


def test_two_acceptances_first_wins(book):
    plan = propose(book, "A", "B", mode="top_two")
    book.approve(plan.id, T0, "clinical_lead")
    a, b = (book.offers[o] for o in plan.offer_ids)
    assert a.status == b.status == "sent"
    r1 = book.accept(b.id, T0 + timedelta(minutes=2))
    r2 = book.accept(a.id, T0 + timedelta(minutes=2, seconds=30))
    assert r1["ok"] and not r2["ok"] and r2["message"] == "This shift is already filled. Thank you."
    assert plan.status == "filled" and plan.filled_by == ["B"]
    assert any(e["action"] == "accept_after_filled" for e in book.audit)


def test_two_slots_need_two_acceptances(book):
    plan = propose(book, "A", "B", "C", slots=2, mode="top_two")
    book.approve(plan.id, T0, "clinical_lead")
    a, b = (book.offers[o] for o in plan.offer_ids)
    assert book.accept(a.id, T0 + timedelta(minutes=1))["ok"]
    assert plan.status == "approved"
    assert book.accept(b.id, T0 + timedelta(minutes=2))["ok"]
    assert plan.status == "filled"


def test_decline_moves_on_and_exhausted_plan_escalates(book):
    plan = propose(book, "A", "B")
    book.approve(plan.id, T0, "clinical_lead")
    book.decline(plan.offer_ids[0], T0 + timedelta(minutes=1))
    book.decline(plan.offer_ids[1], T0 + timedelta(minutes=2))
    assert plan.status == "exhausted"
    assert any(o["kind"] == "internal" and "Escalate" in o["text"] for o in book.notifier.outbox)


def test_one_open_offer_per_physician_and_weekly_cap(book):
    p1 = propose(book, "A")
    book.approve(p1.id, T0, "clinical_lead")
    p2 = book.propose(T0, "coordinator", "split_shift", "Other gap", ["gap-H2"], ["H2"], "projected coverage gap",
                      [cand("A", 1), cand("B", 2)], None)
    book.approve(p2.id, T0, "clinical_lead")
    assert [book.offers[o].physician_id for o in p2.offer_ids] == ["B"]
    assert any(e["action"] == "skipped_at_send" and e["physician_id"] == "A" for e in book.audit)

    capped = OfferBook(book.cfg, Notifier(), None, NAMES)
    for i in range(3):
        p = capped.propose(T0, "c", "split_shift", f"t{i}", [f"g{i}"], ["H1"], "coverage gap now", [cand("A", 1)], None)
        capped.approve(p.id, T0, "lead")
        capped.decline(p.offer_ids[0], T0)
    p = capped.propose(T0, "c", "split_shift", "t4", ["g4"], ["H1"], "coverage gap now", [cand("A", 1)], None)
    capped.approve(p.id, T0, "lead")
    assert not p.offer_ids and p.status == "exhausted"


def test_retraction_when_gap_closes(book):
    plan = propose(book, "A")
    book.approve(plan.id, T0, "clinical_lead")
    book.tick(T0 + timedelta(minutes=3), set(), {})
    offer = book.offers[plan.offer_ids[0]]
    assert plan.status == "cancelled" and offer.status == "cancelled"
    reply = book.accept(offer.id, T0 + timedelta(minutes=4))
    assert not reply["ok"] and reply["code"] == "filled"


def test_no_show_moves_to_backup(book):
    plan = propose(book, "A", "B")
    book.approve(plan.id, T0, "clinical_lead")
    a = book.offers[plan.offer_ids[0]]
    book.accept(a.id, T0 + timedelta(minutes=1))
    assert a.accepted_online_at == T0 + timedelta(minutes=16)   # accept time + 15 min to get online
    book.tick(a.accepted_online_at + timedelta(minutes=10), {"now-H1"}, {})
    assert a.no_show and plan.status == "approved"
    assert book.offers[plan.offer_ids[1]].physician_id == "B"


def test_shift_started_confirms_arrival(book):
    plan = propose(book, "A")
    book.approve(plan.id, T0, "clinical_lead")
    a = book.offers[plan.offer_ids[0]]
    book.accept(a.id, T0 + timedelta(minutes=1))
    book.tick(a.accepted_online_at + timedelta(minutes=10), {"now-H1"}, {"A": T0 + timedelta(minutes=15)})
    assert not a.no_show and a.online_confirmed_at == T0 + timedelta(minutes=15)


def test_backup_approver(book):
    plan = propose(book, "A")
    book.tick(T0 + timedelta(minutes=4), {"now-H1"}, {})
    assert plan.backup_escalated_at is None
    book.tick(T0 + timedelta(minutes=5), {"now-H1"}, {})
    assert plan.backup_escalated_at is not None
    assert any(o.get("audience") == "backup_clinical_lead" for o in book.notifier.outbox)


def test_every_message_is_logged_with_exact_text(book):
    plan = propose(book, "A")
    book.approve(plan.id, T0, "clinical_lead")
    sent = [e for e in book.audit if e["action"] == "sent"][0]
    assert sent["message"] == book.offers[plan.offer_ids[0]].message
    assert "Suncrest Medical Pavilion and Oak Valley Medical" in sent["message"]
    assert "CON-" not in sent["message"]


def test_book_round_trips_through_storage(book):
    plan = propose(book, "A")
    book.approve(plan.id, T0, "clinical_lead")
    data = book.to_dict()
    other = OfferBook(book.cfg, Notifier(), None, NAMES)
    other.load(data, book.audit)
    assert other.offers[plan.offer_ids[0]].expires_at == book.offers[plan.offer_ids[0]].expires_at
    assert other.propose(T0, "c", "split_shift", "x", ["g9"], ["H1"], "coverage gap now", [cand("B", 1)], None).id \
        == "plan-0002"


# ------------------------------------------------------------------ privacy

def test_message_template_has_only_whitelisted_fields():
    text = build_offer_message(["Suncrest Medical Pavilion", "Oak Valley Medical"], T0, T0 + timedelta(hours=3),
                               "coverage gap now", ["credentialed", "rested"], T0 + timedelta(minutes=10))
    assert text.startswith("Sevaro coverage request: Suncrest Medical Pavilion and Oak Valley Medical, 6:20 PM to 9:20 PM")
    check_message(text, {"CON-2105"}, {"Stroke consult request received"})
    with pytest.raises(PrivacyViolation):
        build_offer_message(["X"], T0, T0, "patient in bay 4", [], T0)
    with pytest.raises(PrivacyViolation):
        build_offer_message(["X"], T0, T0, "coverage gap now", ["patient_age"], T0)


@pytest.mark.parametrize("bad", [
    "Please cover CON-2105 at Oak Valley.",
    "Note: Stroke consult request received at 6:07 PM.",
    "Patient waiting for CT review.",
    "A 67 year-old needs you.",
])
def test_privacy_guard_blocks_patient_details(bad):
    with pytest.raises(PrivacyViolation):
        check_message(bad, {"CON-2105"}, {"Stroke consult request received"})
