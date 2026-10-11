"""Offer workflow and audit log.

proposed -> approved | rejected; approved plans send offers one at a time (or the top two for an urgent
stroke gap); each offer: sent -> accepted | declined | expired | cancelled | filled.
The clinical lead approves a plan once; Relay then works down the ranked list on decline or expiry.
Every step is written to an append-only audit log. Offers live in an actions store, never in the event stream.
"""
from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .coverage import PlannedJoin
from .privacy import build_offer_message, check_message
from .timeutil import fmt_clock

OPEN_OFFER = {"sent"}
LIVE_PLAN = {"pending_approval", "approved"}


def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _dt(s):
    return datetime.fromisoformat(s) if isinstance(s, str) else s


@dataclass
class Candidate:
    physician_id: str
    name: str
    rank: int
    start: datetime
    end: datetime
    online_at: datetime
    minutes_to_online: int
    hospitals: list[str]
    channel: str
    why_keys: list[str]


@dataclass
class Offer:
    id: str
    plan_id: str
    physician_id: str
    name: str
    rank: int
    hospitals: list[str]
    start: datetime
    end: datetime
    minutes_to_online: int
    channel: str
    status: str = "sent"
    message: str = ""
    sent_at: datetime | None = None
    expires_at: datetime | None = None
    responded_at: datetime | None = None
    accepted_online_at: datetime | None = None      # when coverage starts after acceptance
    online_confirmed_at: datetime | None = None
    no_show: bool = False
    history: list[dict] = field(default_factory=list)
    delivery: dict = field(default_factory=dict)    # how the message went out: channel, status, Twilio SID, error


@dataclass
class OfferPlan:
    id: str
    kind: str                         # split_shift | start_early
    title: str
    gap_ids: list[str]
    hospitals: list[str]
    reason: str
    slots: int
    mode: str                         # one_at_a_time | top_two
    candidates: list[Candidate]
    eligibility: dict | None
    proposed_by: str
    proposed_at: datetime
    status: str = "pending_approval"
    approved_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = ""
    backup_escalated_at: datetime | None = None
    next_index: int = 0
    offer_ids: list[str] = field(default_factory=list)
    filled_by: list[str] = field(default_factory=list)


class OfferBook:
    def __init__(self, cfg, notifier, storage=None, hospital_names: dict | None = None):
        self.cfg = cfg
        self.notifier = notifier
        self.storage = storage
        self.hospital_names = hospital_names or {}
        self.plans: dict[str, OfferPlan] = {}
        self.offers: dict[str, Offer] = {}
        self.audit: list[dict] = []
        self._ids = {"plan": itertools.count(1), "off": itertools.count(1)}
        self.consult_ids: set[str] = set()
        self.descriptions: set[str] = set()

    # ------------------------------------------------------------ helpers
    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids[prefix]):04d}"

    def log(self, now: datetime, actor: str, action: str, **detail) -> dict:
        entry = {"seq": len(self.audit) + 1, "at": now.isoformat(), "actor": actor, "action": action, **detail}
        self.audit.append(entry)
        if self.storage:
            self.storage.append_audit(entry)
        return entry

    def _offer_event(self, offer: Offer, now: datetime, status: str, by: str, note: str = "") -> None:
        offer.status = status
        offer.history.append({"at": now.isoformat(), "status": status, "by": by, "note": note})

    def history_for(self, physician_id: str, now: datetime) -> dict:
        week_ago = now - timedelta(days=7)
        sent = [o for o in self.offers.values() if o.physician_id == physician_id and o.sent_at and o.sent_at >= week_ago]
        open_offer = next((o.id for o in sent if o.status in OPEN_OFFER), None)
        accepted = next((o for o in self.offers.values() if o.physician_id == physician_id
                         and o.status == "accepted" and not o.no_show and o.end > now), None)
        return {"offers_this_week": len(sent), "open_offer": open_offer,
                "accepted_offer": accepted.id if accepted else None,
                "accepted_window": (f"{fmt_clock(accepted.start)} to {fmt_clock(accepted.end)}" if accepted else None)}

    def histories(self, now: datetime) -> dict[str, dict]:
        pids = {o.physician_id for o in self.offers.values()}
        return {pid: self.history_for(pid, now) for pid in pids}

    def joins(self) -> list[PlannedJoin]:
        out = []
        for o in self.offers.values():
            if o.status != "accepted" or o.no_show:
                continue
            plan = self.plans[o.plan_id]
            limit = self.cfg.get_path("flood_guard.limit_to_gap_hospitals", True) and plan.kind == "split_shift"
            out.append(PlannedJoin(o.physician_id, o.accepted_online_at or o.start, o.end,
                                   set(o.hospitals) if limit else None, o.id, kind=plan.kind))
        return out

    # ------------------------------------------------------------ commands
    def propose(self, now: datetime, by: str, kind: str, title: str, gap_ids: list[str], hospitals: list[str],
                reason: str, candidates: list[Candidate], eligibility: dict | None, slots: int = 1,
                mode: str = "one_at_a_time") -> OfferPlan:
        if not candidates:
            raise ValueError("No eligible candidates. Escalate to the clinical lead instead of proposing an offer.")
        for g in gap_ids:
            for p in self.plans.values():
                if p.status in LIVE_PLAN and g in p.gap_ids and p.kind == kind:
                    raise ValueError(f"Plan {p.id} already covers {g}.")
        plan = OfferPlan(self._next_id("plan"), kind, title, gap_ids, hospitals, reason, slots, mode, candidates,
                         eligibility, by, now)
        self.plans[plan.id] = plan
        self.log(now, by, "proposed", plan_id=plan.id, kind=kind, title=title, gap_ids=gap_ids,
                 ranked=[{"physician_id": c.physician_id, "name": c.name, "rank": c.rank,
                          "window": f"{fmt_clock(c.start)} to {fmt_clock(c.end)}"} for c in candidates],
                 eligibility=eligibility)
        self.notifier.internal(f"Approval needed: {title}", audience="clinical_lead")
        return plan

    def approve(self, plan_id: str, now: datetime, by: str) -> OfferPlan:
        plan = self.plans[plan_id]
        if plan.status != "pending_approval":
            raise ValueError(f"Plan {plan_id} is {plan.status}, not waiting for approval.")
        plan.status, plan.approved_by, plan.decided_at = "approved", by, now
        self.log(now, by, "approved", plan_id=plan_id)
        self._send_next(plan, now, count=2 if plan.mode == "top_two" else 1)
        return plan

    def reject(self, plan_id: str, now: datetime, by: str, note: str = "") -> OfferPlan:
        plan = self.plans[plan_id]
        if plan.status != "pending_approval":
            raise ValueError(f"Plan {plan_id} is {plan.status}, not waiting for approval.")
        plan.status, plan.approved_by, plan.decided_at, plan.decision_note = "rejected", by, now, note
        self.log(now, by, "rejected", plan_id=plan_id, note=note)
        return plan

    def cancel(self, plan_id: str, now: datetime, by: str, reason: str) -> OfferPlan:
        plan = self.plans[plan_id]
        plan.status = "cancelled"
        for oid in plan.offer_ids:
            o = self.offers[oid]
            if o.status in OPEN_OFFER:
                self._offer_event(o, now, "cancelled", by, reason)
        self.log(now, by, "plan_cancelled", plan_id=plan_id, reason=reason)
        return plan

    def accept(self, offer_id: str, now: datetime) -> dict:
        """First acceptance wins. Late, duplicate, or clashing replies get a polite answer and a log entry."""
        o = self.offers[offer_id]
        plan = self.plans[o.plan_id]
        if o.status == "expired":
            self.log(now, o.physician_id, "late_accept_rejected", plan_id=plan.id, offer_id=o.id)
            return {"ok": False, "code": "expired",
                    "message": f"This offer expired at {fmt_clock(o.expires_at)}. Thank you for replying."}
        if o.status in ("filled", "cancelled") or plan.status in ("filled", "cancelled", "rejected"):
            self.log(now, o.physician_id, "accept_after_filled", plan_id=plan.id, offer_id=o.id)
            if o.status in OPEN_OFFER:
                self._offer_event(o, now, "filled", "system", "already filled")
            return {"ok": False, "code": "filled", "message": "This shift is already filled. Thank you."}
        if o.status != "sent":
            return {"ok": False, "code": o.status, "message": f"This offer is already {o.status}."}
        if not self._try_fill(plan, o.physician_id):
            self._offer_event(o, now, "filled", "system", "slot taken by an earlier acceptance")
            self.log(now, o.physician_id, "accept_after_filled", plan_id=plan.id, offer_id=o.id)
            return {"ok": False, "code": "filled", "message": "This shift is already filled. Thank you."}
        o.responded_at = now
        o.accepted_online_at = max(o.start, now + timedelta(minutes=o.minutes_to_online))
        self._offer_event(o, now, "accepted", o.physician_id)
        self.log(now, o.physician_id, "accepted", plan_id=plan.id, offer_id=o.id,
                 online_at=o.accepted_online_at.isoformat())
        if len(plan.filled_by) >= plan.slots:
            plan.status = "filled"
            for oid in plan.offer_ids:
                other = self.offers[oid]
                if other.id != o.id and other.status in OPEN_OFFER:
                    self._offer_event(other, now, "filled", "system", "filled by an earlier acceptance")
                    self.log(now, "system", "offer_retracted", plan_id=plan.id, offer_id=other.id, reason="filled")
            self.log(now, "system", "plan_filled", plan_id=plan.id, filled_by=plan.filled_by)
        self.notifier.internal(f"{o.name} accepted: {plan.title}", audience="coordinator")
        return {"ok": True, "code": "accepted",
                "message": f"Thank you. You are confirmed from {fmt_clock(o.accepted_online_at)} to {fmt_clock(o.end)}.",
                "online_at": o.accepted_online_at.isoformat()}

    def _try_fill(self, plan: OfferPlan, physician_id: str) -> bool:
        if self.storage and hasattr(self.storage, "try_fill_slot"):
            if not self.storage.try_fill_slot(plan.id, physician_id, plan.slots):
                return False
        if len(plan.filled_by) >= plan.slots or physician_id in plan.filled_by:
            return False
        plan.filled_by.append(physician_id)
        return True

    def decline(self, offer_id: str, now: datetime) -> dict:
        o = self.offers[offer_id]
        if o.status != "sent":
            self.log(now, o.physician_id, "late_decline_ignored", offer_id=o.id, status=o.status)
            return {"ok": False, "code": o.status, "message": "Thank you. This offer is no longer open."}
        o.responded_at = now
        self._offer_event(o, now, "declined", o.physician_id)
        self.log(now, o.physician_id, "declined", plan_id=o.plan_id, offer_id=o.id)
        self._send_next(self.plans[o.plan_id], now)
        return {"ok": True, "code": "declined", "message": "Thank you for letting us know."}

    def record_delivery_status(self, message_sid: str, status: str, now: datetime, error_code: str | None = None) -> bool:
        """Twilio's status callback: queued, sent, delivered, undelivered, or failed."""
        for o in self.offers.values():
            if o.delivery.get("twilio_sid") == message_sid:
                o.delivery["status"] = status
                if error_code:
                    o.delivery["error_code"] = error_code
                self.log(now, "system", "sms_status", offer_id=o.id, physician_id=o.physician_id, status=status)
                return True
        return False

    def online(self, offer_id: str, now: datetime) -> dict:
        o = self.offers[offer_id]
        if o.status != "accepted":
            return {"ok": False, "message": "This offer is not accepted."}
        o.online_confirmed_at = now
        self.log(now, o.physician_id, "online", offer_id=o.id)
        return {"ok": True, "message": "Thank you. You are marked online."}

    # ------------------------------------------------------------ sending
    def _send_next(self, plan: OfferPlan, now: datetime, count: int = 1) -> None:
        if plan.status != "approved":
            return
        sent = 0
        while sent < count and plan.next_index < len(plan.candidates):
            c = plan.candidates[plan.next_index]
            plan.next_index += 1
            hist = self.history_for(c.physician_id, now)
            max_week = int(self.cfg.get_path("split_shift.max_offers_per_week", 3))
            if hist["open_offer"]:
                self.log(now, "system", "skipped_at_send", plan_id=plan.id, physician_id=c.physician_id,
                         reason=f"Already has open offer {hist['open_offer']}.")
                continue
            if hist["offers_this_week"] >= max_week:
                self.log(now, "system", "skipped_at_send", plan_id=plan.id, physician_id=c.physician_id,
                         reason=f"Already received {hist['offers_this_week']} offers this week.")
                continue
            self._send(plan, c, now)
            sent += 1
        if sent == 0 and not any(self.offers[o].status in OPEN_OFFER for o in plan.offer_ids) \
                and len(plan.filled_by) < plan.slots:
            plan.status = "exhausted"
            self.log(now, "system", "plan_exhausted", plan_id=plan.id,
                     note="Nobody on the ranked list accepted. Escalating to the clinical lead; no rule is bent.")
            self.notifier.internal(f"No doctor accepted: {plan.title}. Escalate.", audience="clinical_lead")

    def _send(self, plan: OfferPlan, c: Candidate, now: datetime) -> Offer:
        expiry = timedelta(minutes=float(self.cfg.get_path("split_shift.offer_expiry_minutes", 10)))
        names = [self.hospital_names.get(h, h) for h in c.hospitals]
        text = build_offer_message(names, c.start, c.end, plan.reason, c.why_keys, now + expiry)
        check_message(text, self.consult_ids, self.descriptions)
        o = Offer(self._next_id("off"), plan.id, c.physician_id, c.name, c.rank, list(c.hospitals), c.start, c.end,
                  c.minutes_to_online, c.channel, message=text, sent_at=now, expires_at=now + expiry)
        o.history.append({"at": now.isoformat(), "status": "sent", "by": plan.approved_by or "system", "note": ""})
        self.offers[o.id] = o
        plan.offer_ids.append(o.id)
        delivery = self.notifier.offer(c.physician_id, c.channel, text, o.id, name=c.name)
        o.delivery = delivery
        o.channel = delivery.get("channel", c.channel)
        self.log(now, "system", "sent", plan_id=plan.id, offer_id=o.id, physician_id=c.physician_id,
                 channel=o.channel, message=text, delivery=delivery, expires_at=o.expires_at.isoformat())
        return o

    # ------------------------------------------------------------ timers
    def tick(self, now: datetime, open_gap_ids: set[str], shift_started: dict[str, datetime]) -> list[dict]:
        """Expiry, backup approver, no-shows, and retraction when a gap closes. Returns what changed."""
        changes = []
        backup_after = timedelta(minutes=float(self.cfg.get_path("split_shift.backup_approver_minutes", 5)))
        grace = timedelta(minutes=float(self.cfg.get_path("split_shift.no_show_grace_minutes", 10)))
        for o in list(self.offers.values()):
            if o.status == "sent" and o.expires_at and now >= o.expires_at:
                self._offer_event(o, now, "expired", "system", "no reply in time")
                changes.append(self.log(now, "system", "expired", plan_id=o.plan_id, offer_id=o.id))
                self._send_next(self.plans[o.plan_id], now)
            if (o.status == "accepted" and not o.no_show and not o.online_confirmed_at
                    and o.accepted_online_at and now >= o.accepted_online_at + grace):
                started = shift_started.get(o.physician_id)
                if started and started >= (o.responded_at or o.sent_at):
                    o.online_confirmed_at = started
                    continue
                o.no_show = True
                plan = self.plans[o.plan_id]
                if o.physician_id in plan.filled_by:
                    plan.filled_by.remove(o.physician_id)
                plan.status = "approved"
                changes.append(self.log(now, "system", "no_show", plan_id=plan.id, offer_id=o.id,
                                        note="Accepted but not online; moving to the next doctor on the list."))
                self.notifier.internal(f"{o.name} has not come online. Moving to the backup.", audience="coordinator")
                self._send_next(plan, now)
        for plan in self.plans.values():
            if plan.status == "pending_approval" and not plan.backup_escalated_at and now - plan.proposed_at >= backup_after:
                plan.backup_escalated_at = now
                changes.append(self.log(now, "system", "approval_escalated", plan_id=plan.id,
                                        note="No decision yet; request sent to the backup clinical lead."))
                self.notifier.internal(f"Backup approval needed: {plan.title}", audience="backup_clinical_lead")
            if plan.status in LIVE_PLAN and plan.gap_ids and not (set(plan.gap_ids) & open_gap_ids) \
                    and len(plan.filled_by) < plan.slots:
                self.cancel(plan.id, now, "system", "The gap closed before anyone accepted.")
                changes.append({"action": "plan_cancelled", "plan_id": plan.id})
        return changes

    # ------------------------------------------------------------ views
    def offer_json(self, o: Offer, now: datetime) -> dict:
        plan = self.plans[o.plan_id]
        remaining = int((o.expires_at - now).total_seconds()) if o.expires_at and o.status == "sent" else None
        return {
            "id": o.id, "plan_id": o.plan_id, "physician_id": o.physician_id, "physician_name": o.name,
            "rank": o.rank, "hospitals": [{"id": h, "name": self.hospital_names.get(h, h)} for h in o.hospitals],
            "start": o.start.isoformat(), "end": o.end.isoformat(),
            "window_label": f"{fmt_clock(o.start)} to {fmt_clock(o.end)} ET",
            "minutes_to_online": o.minutes_to_online, "channel": o.channel,
            "channel_label": "app notification" if o.channel == "app_push" else "text message",
            "status": o.status, "message": o.message, "sent_at": _iso(o.sent_at), "expires_at": _iso(o.expires_at),
            "seconds_left": max(0, remaining) if remaining is not None else None,
            "responded_at": _iso(o.responded_at), "online_at": _iso(o.accepted_online_at),
            "online_confirmed_at": _iso(o.online_confirmed_at), "no_show": o.no_show,
            "kind": plan.kind, "history": o.history, "delivery": o.delivery,
        }

    def plan_json(self, plan: OfferPlan, now: datetime) -> dict:
        d = {k: _iso(v) for k, v in asdict(plan).items() if k not in ("candidates", "eligibility")}
        d["candidates"] = [{"physician_id": c.physician_id, "name": c.name, "rank": c.rank,
                            "window_label": f"{fmt_clock(c.start)} to {fmt_clock(c.end)} ET",
                            "hospitals": [self.hospital_names.get(h, h) for h in c.hospitals],
                            "minutes_to_online": c.minutes_to_online, "channel": c.channel}
                           for c in plan.candidates]
        d["eligibility"] = plan.eligibility
        d["offers"] = [self.offer_json(self.offers[oid], now) for oid in plan.offer_ids]
        d["hospital_names"] = [self.hospital_names.get(h, h) for h in plan.hospitals]
        return d

    def to_dict(self) -> dict:
        return {"plans": [self._plan_store(p) for p in self.plans.values()],
                "offers": [{k: _iso(v) for k, v in asdict(o).items()} for o in self.offers.values()],
                "next_plan": len(self.plans) + 1, "next_offer": len(self.offers) + 1}

    @staticmethod
    def _plan_store(p: OfferPlan) -> dict:
        d = asdict(p)
        return {k: (_iso(v) if not isinstance(v, list) else
                    [{kk: _iso(vv) for kk, vv in c.items()} if isinstance(c, dict) else c for c in v])
                for k, v in d.items()}

    def load(self, data: dict, audit: list[dict]) -> None:
        dt_fields_plan = ("proposed_at", "decided_at", "backup_escalated_at")
        for raw in data.get("plans", []):
            raw = dict(raw)
            raw["candidates"] = [Candidate(**{**c, "start": _dt(c["start"]), "end": _dt(c["end"]),
                                              "online_at": _dt(c["online_at"])}) for c in raw["candidates"]]
            for f in dt_fields_plan:
                raw[f] = _dt(raw.get(f))
            plan = OfferPlan(**raw)
            self.plans[plan.id] = plan
        for raw in data.get("offers", []):
            raw = dict(raw)
            for f in ("start", "end", "sent_at", "expires_at", "responded_at", "accepted_online_at",
                      "online_confirmed_at"):
                raw[f] = _dt(raw.get(f))
            o = Offer(**raw)
            self.offers[o.id] = o
        self._ids = {"plan": itertools.count(int(data.get("next_plan", len(self.plans) + 1))),
                     "off": itertools.count(int(data.get("next_offer", len(self.offers) + 1)))}
        self.audit = list(audit)
