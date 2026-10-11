"""RelayService: the one object the API talks to.

State = replay(events) + actions. Events come from the kit plus every appended file; actions (offers,
approvals, replies, coordinator actions) live in their own store. Nothing here needs a restart.
"""
from __future__ import annotations

import csv
import io
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .analysis import analyze
from .clock import Clock
from .config import REPO_ROOT, load_config
from .coverage import PlannedJoin
from .dataset import Dataset
from . import dataset_import
from .loader import EVENT_COLUMNS
from .metrics import publish
from .notify import Notifier, make_notifier
from .offers import Candidate, OfferBook
from .serialize import analysis_json
from .insights import credentialing_impact, handoffs, scoreboard, time_to_doctor
from .simulate import simulate_flood_guard
from .summary import plan_summary
from .voice import speak
from .storage import make_storage
from .timeline import replay
from .timeutil import fmt_clock, iso, minutes_between


class RelayService:
    def __init__(self, cfg=None, storage=None, notifier: Notifier | None = None):
        self.cfg = cfg or load_config()
        self.storage = storage or make_storage(REPO_ROOT / "data" / "state")
        self.notifier = notifier or make_notifier()
        self.lock = threading.RLock()
        self._build()

    # ------------------------------------------------------------ setup
    # ------------------------------------------------------------ which dataset
    @property
    def _datasets_root(self):
        return Path(getattr(self.storage, "root", REPO_ROOT / "data" / "state")) / "datasets"

    def _active_dataset(self) -> dict | None:
        p = self._datasets_root / "active.json"
        if not p.exists():
            return None
        info = json.loads(p.read_text(encoding="utf-8"))
        return info if Path(info.get("dir", "")).is_dir() else None

    def _make_dataset(self) -> Dataset:
        info = self._active_dataset()
        if not info:
            return Dataset(self.cfg)
        cfg = self.cfg.with_overrides({"clock": {"default_now": "latest"}})
        return Dataset(cfg, info["dir"])

    def dataset_info(self) -> dict:
        info = self._active_dataset()
        ref = self.ds.ref
        out = {"source": "uploaded" if info else "sample", "name": info["name"] if info else "Sample kit (6:20 PM)",
               "loaded_at": info.get("loaded_at") if info else None,
               "hospitals": len(ref.hospitals), "physicians": len(ref.physicians), "events": len(self.ds.events),
               "scenario_date": ref.scenario_date.isoformat() if ref.scenario_date else None}
        return out

    def load_dataset(self, files: list[tuple[str, bytes]], name: str = "") -> dict:
        """Replace the whole network with an uploaded dataset. Clears offers, actions and added events."""
        try:
            texts = dataset_import.expand(files)
        except Exception as exc:  # a broken zip or a file that is not text
            return {"ok": False, "errors": [f"Could not read the upload: {exc}"], "files": []}
        result = dataset_import.classify(texts)
        public = {k: v for k, v in result.items() if k != "roles"}
        if result["errors"]:
            return {"ok": False, **public}
        with self.lock:
            folder = dataset_import.write(result["roles"], self._datasets_root)
            zips = [n for n, _ in files if n.lower().endswith(".zip")]
            label = name or (Path(zips[0]).stem if len(zips) == 1 and len(files) == 1
                             else f"Uploaded dataset ({len(result['roles'])} files)")
            (self._datasets_root / "active.json").write_text(json.dumps(
                {"dir": str(folder), "name": label, "loaded_at": datetime.now().astimezone().isoformat()}),
                encoding="utf-8")
            self.reset()
            an = self.analysis()
            top = an.groups[0] if an.groups else None
            report = self.ds.load_report.to_dict()
            return {"ok": True, **public, "dataset": self.dataset_info(), "now": self.now().isoformat(),
                    "skipped_rows": report["skipped_rows"], "load_warnings": report["warnings"],
                    "issues": len(an.groups),
                    "top_issue": ({"group": top.title, "what": top.top.what} if top else None)}

    def use_sample_dataset(self) -> dict:
        with self.lock:
            p = self._datasets_root / "active.json"
            if p.exists():
                p.unlink()
            self.reset()
            return {"ok": True, "dataset": self.dataset_info(), "now": self.now().isoformat()}

    def _build(self) -> None:
        self.ds = self._make_dataset()
        for name, text in self.storage.list_uploads():
            self.ds.append_csv_text(text, name)
        self.clock = Clock()
        names = {h.id: h.name for h in self.ds.ref.hospitals.values()}
        self.book = OfferBook(self.cfg, self.notifier, self.storage, names)
        saved = self.storage.load_actions()
        if saved.get("book"):
            self.book.load(saved["book"], self.storage.load_audit())
        self.coordinator_actions: list[dict] = saved.get("coordinator_actions", [])
        self.acks: dict[str, str] = saved.get("acks", {})
        self.alerted: set[str] = set(saved.get("alerted", []))
        self.first_seen: dict[str, datetime] = {}     # issue id -> engine time it was first seen
        self._refresh_privacy_sets()
        self._cache = None

    def _refresh_privacy_sets(self) -> None:
        self.book.consult_ids = {e.consult_id for e in self.ds.events if e.consult_id}
        self.book.descriptions = {e.description for e in self.ds.events if e.description}

    def _save(self) -> None:
        self.storage.save_actions({"book": self.book.to_dict(), "coordinator_actions": self.coordinator_actions,
                                   "acks": self.acks, "alerted": sorted(self.alerted)})
        self._cache = None

    def now(self) -> datetime:
        return self.clock.now(self.ds.default_now())

    # ------------------------------------------------------------ state
    def analysis(self, now: datetime | None = None):
        now = now or self.now()
        key = (len(self.ds.events), len(self.book.audit), len(self.coordinator_actions), now)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        joins = self.book.joins()
        world = replay(self.ds, now, joins)
        an = analyze(self.ds, world, joins, self.book.histories(now))
        self._cache = (key, an)
        return an

    def state(self) -> dict:
        with self.lock:
            now = self.now()
            an = self.analysis(now)
            out = analysis_json(self.ds, an)
            for group in out["issues"]:
                for issue in group["issues"]:
                    issue["acknowledged_at"] = self.acks.get(issue["id"])
                    issue["plan_id"] = self._plan_for_issue(issue)
            out["clock"] = self.clock.to_dict(self.ds.default_now())
            out["dataset"] = self.dataset_info()
            out["plans"] = [self.book.plan_json(p, now) for p in self.book.plans.values()]
            issues_flat = [i for g in out["issues"] for i in g["issues"]]
            for p in out["plans"]:
                p["summary"] = self._summary(p, issues_flat)
            out["handoff_confirmations"] = {a["consult_id"]: a["at"] for a in self.coordinator_actions
                                            if a["kind"] == "handoff_confirmed" and a.get("status") != "undone"}
            out["offers"] = [self.book.offer_json(o, now) for o in self.book.offers.values()]
            out["pending_approvals"] = [p["id"] for p in out["plans"] if p["status"] == "pending_approval"]
            out["coordinator_actions"] = self.coordinator_actions[-50:]
            out["joins"] = [{"physician_id": j.physician_id, "online_at": j.online_at.isoformat(),
                             "end": j.end.isoformat(), "hospitals": sorted(j.hospitals) if j.hospitals else None,
                             "kind": j.kind, "offer_id": j.label} for j in self.book.joins()]
            out["outbox"] = self.notifier.outbox[-30:]
            twilio = getattr(self.notifier, "cfg", None)
            out["messaging"] = {"sms": twilio is not None, "shared_phone": bool(twilio and twilio.demo_phone)}
            out["audit_count"] = len(self.book.audit)
            out["summary"]["pending_approvals"] = len(out["pending_approvals"])
            return out

    def _plan_for_issue(self, issue: dict) -> str | None:
        plan = (issue.get("what_to_do") or {}).get("primary") or {}
        detail = plan.get("detail") or {}
        if detail.get("offer_id") in self.book.offers:
            return self.book.offers[detail["offer_id"]].plan_id
        gap_ids = set((detail.get("offer") or {}).get("gap_ids") or [])
        for p in self.book.plans.values():
            if p.status in ("pending_approval", "approved", "filled") and (
                    gap_ids & set(p.gap_ids) or (plan.get("physician_id") and any(
                        c.physician_id == plan.get("physician_id") for c in p.candidates)
                        and plan.get("kind") in ("split_shift", "start_early", "covered_by_offer"))):
                return p.id
        return None

    # ------------------------------------------------------------ events
    def upload(self, text: str, name: str) -> dict:
        with self.lock:
            before = {i.id: i for i in self.analysis().issues}
            columns = next(csv.reader(io.StringIO(text.lstrip("﻿"))), [])
            missing = [c for c in EVENT_COLUMNS if c not in [x.strip() for x in columns]]
            if missing:
                return {"ok": False, "error": f"Missing columns: {', '.join(missing)}", "missing_columns": missing}
            location = self.storage.save_upload(name, text)
            report = self.ds.append_csv_text(text, name).to_dict()
            self.clock.after_append(self.ds.default_now())
            self._refresh_privacy_sets()
            self._cache = None
            self._save_processed()
            self.tick()
            after = {i.id: i for i in self.analysis().issues}
            top = self.analysis().groups[0] if self.analysis().groups else None
            report.update({
                "ok": True, "stored_at": location, "now": self.now().isoformat(),
                "new_issues": [{"id": i, "title": after[i].title, "what": after[i].what} for i in after if i not in before],
                "resolved_issues": [{"id": i, "title": before[i].title} for i in before if i not in after],
                "top_issue": ({"group": top.title, "what": top.top.what, "why": top.top.why,
                               "do": top.top.plan.primary.label if top.top.plan else None} if top else None),
            })
            return report

    def add_event(self, fields: dict) -> dict:
        row = {c: str(fields.get(c, "") or "") for c in EVENT_COLUMNS}
        if not row["event_id"]:
            row["event_id"] = f"EVT-UI-{len(self.ds.events) + 1:05d}"
        now = self.now()
        if not row["event_time"]:
            row["event_time"] = now.isoformat()
        if not row["ingested_at"]:
            row["ingested_at"] = row["event_time"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=EVENT_COLUMNS)
        writer.writeheader()
        writer.writerow(row)
        return self.upload(buf.getvalue(), f"single_{row['event_id']}.csv")

    def reload_events(self) -> dict:
        """Rebuild the day from the kit plus every stored events file (called by the ingest Lambda)."""
        with self.lock:
            self.ds = self._make_dataset()
            for name, text in self.storage.list_uploads():
                self.ds.append_csv_text(text, name)
            self.clock.after_append(self.ds.default_now())
            self._refresh_privacy_sets()
            self._cache = None
            self._save_processed()
            self.tick()
            return {"ok": True, "events": len(self.ds.events), "files": self.ds.appended_sources,
                    "now": self.now().isoformat()}

    def reset(self) -> None:
        with self.lock:
            self.storage.clear_uploads()
            self.storage.reset_actions()
            self.notifier.outbox.clear()
            self._build()

    # ------------------------------------------------------------ clock
    def clock_cmd(self, action: str, minutes: float | None = None, to: str | None = None,
                  speed: float | None = None) -> dict:
        with self.lock:
            data_now = self.ds.default_now()
            if action == "advance":
                self.clock.advance(float(minutes or 1), data_now)
            elif action == "set" and to:
                t = datetime.fromisoformat(to)
                if t.tzinfo is None:
                    from .timeutil import ET
                    t = t.replace(tzinfo=ET)
                self.clock.set(t)
            elif action == "play":
                self.clock.play(float(speed or 10), data_now)
            elif action == "pause":
                self.clock.pause(data_now)
            elif action in ("follow_data", "live"):
                self.clock.follow_data()
            else:
                raise ValueError(f"Unknown clock action '{action}'.")
            self._cache = None
            changes = self.tick()
            return {"clock": self.clock.to_dict(data_now), "changes": changes}

    # ------------------------------------------------------------ offers
    def propose(self, issue_id: str | None = None, gap_id: str | None = None, by: str = "coordinator",
                mode: str = "one_at_a_time") -> dict:
        with self.lock:
            now = self.now()
            an = self.analysis(now)
            names = {h.id: h.name for h in self.ds.ref.hospitals.values()}
            result, need_hospitals = None, []
            if issue_id:
                issue = next((i for i in an.issues if i.id == issue_id), None)
                if issue is None:
                    raise KeyError(f"Unknown issue {issue_id}.")
                primary = issue.plan.primary if issue.plan else None
                if primary is None:
                    raise ValueError("This issue has no fix to propose.")
                if primary.kind == "start_early":
                    return self._propose_start_early(an, issue, primary, by)
                if primary.kind == "split_shift" and issue.type == "coverage_gap_projected":
                    gap_id = issue.id.replace("iss-", "", 1)
                elif primary.kind not in ("split_shift", "covered_by_offer"):
                    raise ValueError(f"The recommended fix is '{primary.label}', which is not an offer. "
                                     f"Use it directly{' or escalate' if primary.kind != 'escalate_lead' else ''}.")
            if gap_id and gap_id in an.gap_eligibility:
                result = an.gap_eligibility[gap_id]
            else:
                result = an.cluster
            if result is None or not result.eligible:
                raise ValueError("No off-duty doctor meets every rule. Escalate to the clinical lead; no rule is bent.")
            candidates = []
            for row in result.eligible:
                o = row.offer
                phys = self.ds.ref.physicians[row.physician_id]
                why = ["credentialed", "rested"] + (["fast"] if o.minutes_to_online <= 15 else [])
                candidates.append(Candidate(row.physician_id, row.name, row.rank, o.start, o.end, o.online_at,
                                            o.minutes_to_online, list(o.hospitals), phys.preferred_channel or "app_push",
                                            why))
            best = result.best.offer
            gaps = {g.id: g for g in an.all_gaps}
            reason = "coverage gap now" if any(gaps.get(g) and gaps[g].kind == "now" for g in best.gap_ids) \
                else "projected coverage gap"
            need_hospitals = list(best.hospitals)
            title = (f"Split shift: {', '.join(names[h] for h in need_hospitals)}, "
                     f"{fmt_clock(best.start)} to {fmt_clock(best.end)} ET")
            slots = 1
            plan = self.book.propose(now, by, "split_shift", title, list(best.gap_ids), need_hospitals, reason,
                                     candidates, result.to_dict(names), slots=slots, mode=mode)
            self._save()
            return self.book.plan_json(plan, now)

    def _propose_start_early(self, an, issue, option, by: str) -> dict:
        now = an.world.now
        names = {h.id: h.name for h in self.ds.ref.hospitals.values()}
        pid = option.physician_id
        phys = self.ds.ref.physicians[pid]
        creds = self.ds.ref.credentials.get(pid, set())
        gaps = [g for g in an.now_gaps if g.hospital_id in creds] or [g for g in an.now_gaps
                                                                     if g.hospital_id == issue.where.get("hospital_id")]
        hospitals = sorted({g.hospital_id for g in gaps} or {issue.where.get("hospital_id")})
        scheduled = datetime.fromisoformat(option.detail["scheduled_start"])
        online = int(phys.est_minutes_to_online or 0)
        start = now + timedelta(minutes=online)
        cand = Candidate(pid, phys.name, 1, start, scheduled, start, online, hospitals,
                         phys.preferred_channel or "app_push", ["credentialed", "rested", "fast"])
        title = f"Start early: {phys.name} from {fmt_clock(start)} instead of {fmt_clock(scheduled)}"
        plan = self.book.propose(now, by, "start_early", title, [g.id for g in gaps], hospitals, "coverage gap now",
                                 [cand], {"note": option.why, "hospitals": [names[h] for h in hospitals]})
        self._save()
        return self.book.plan_json(plan, now)

    def _offer_cmd(self, fn, *args, **kwargs):
        with self.lock:
            result = fn(*args, **kwargs)
            self._save()
            return result

    def approve(self, plan_id: str, by: str = "clinical_lead") -> dict:
        plan = self._offer_cmd(self.book.approve, plan_id, self.now(), by)
        return self.book.plan_json(plan, self.now())

    def reject(self, plan_id: str, by: str = "clinical_lead", note: str = "") -> dict:
        plan = self._offer_cmd(self.book.reject, plan_id, self.now(), by, note)
        return self.book.plan_json(plan, self.now())

    def accept(self, offer_id: str) -> dict:
        with self.lock:
            self.tick()          # an offer that has already expired cannot be accepted
            result = self.book.accept(offer_id, self.now())
            self._save()
            return {**result, "offer": self.book.offer_json(self.book.offers[offer_id], self.now())}

    def decline(self, offer_id: str) -> dict:
        with self.lock:
            self.tick()
            result = self.book.decline(offer_id, self.now())
            self._save()
            return {**result, "offer": self.book.offer_json(self.book.offers[offer_id], self.now())}

    def online(self, offer_id: str) -> dict:
        result = self._offer_cmd(self.book.online, offer_id, self.now())
        return {**result, "offer": self.book.offer_json(self.book.offers[offer_id], self.now())}

    def sms_reply(self, from_number: str, body: str) -> str:
        """A doctor's text reply: 1 accepts, 2 declines, ONLINE confirms they are logged in. Returns the text to send back."""
        from .notify import parse_reply

        with self.lock:
            intent = parse_reply(body)
            mine = sorted((o for o in self.book.offers.values() if self.notifier.owns(from_number, o.physician_id)),
                          key=lambda o: o.sent_at or o.start, reverse=True)
            if intent is None:
                return "Reply 1 to accept or 2 to decline."
            if not mine:
                return "There is no open offer for this number."
            now = self.now()
            if intent == "online":
                target = next((o for o in mine if o.status == "accepted"), None)
                if target is None:
                    return "There is no accepted offer to confirm."
                self.book.log(now, target.physician_id, "sms_reply", offer_id=target.id, intent=intent)
                return self.online(target.id)["message"]
            target = mine[0]            # the newest offer; the offer logic answers late, duplicate, or filled replies
            if intent == "accept" and target.status == "accepted":     # a doctor who texts "1" twice is still confirmed
                return (f"You are already confirmed from {fmt_clock(target.accepted_online_at)} to "
                        f"{fmt_clock(target.end)}.")
            self.book.log(now, target.physician_id, "sms_reply", offer_id=target.id, intent=intent)
            result = self.accept(target.id) if intent == "accept" else self.decline(target.id)
            reply = result["message"]
            if result.get("ok") and intent == "accept":
                reply += " Reply ONLINE when you are logged in."
            return reply

    def sms_status(self, message_sid: str, status: str, error_code: str | None = None) -> bool:
        with self.lock:
            found = self.book.record_delivery_status(message_sid, status, self.now(), error_code)
            if found:
                self._save()
            return found

    def offer_view(self, offer_id: str) -> dict:
        """The phone view: hospitals, hours, start time, why you. No patient details."""
        with self.lock:
            self.tick()
            return self.book.offer_json(self.book.offers[offer_id], self.now())

    def physician_offers(self, physician_id: str) -> list[dict]:
        with self.lock:
            now = self.now()
            return [self.book.offer_json(o, now) for o in self.book.offers.values() if o.physician_id == physician_id]

    # ------------------------------------------------------------ coordinator actions
    def coordinator_action(self, kind: str, payload: dict, by: str = "coordinator") -> dict:
        with self.lock:
            now = self.now()
            action = {"id": f"act-{len(self.coordinator_actions) + 1:04d}", "kind": kind, "at": now.isoformat(),
                      "by": by, "status": "requested", **payload}
            if kind == "undo":
                target = next((a for a in self.coordinator_actions if a["id"] == payload.get("action_id")), None)
                if target is None:
                    raise KeyError("Unknown action.")
                target["status"] = "undone"
            self.coordinator_actions.append(action)
            self.book.log(now, by, kind, **payload)
            if kind == "escalate":
                self.notifier.internal(f"Escalation: {payload.get('summary', payload.get('issue_id', ''))}",
                                       audience="clinical_lead")
            self._save()
            return action

    def acknowledge(self, issue_id: str, by: str = "coordinator") -> dict:
        with self.lock:
            now = self.now()
            self.acks[issue_id] = now.isoformat()
            self.book.log(now, by, "acknowledged", issue_id=issue_id)
            self._save()
            return {"issue_id": issue_id, "acknowledged_at": self.acks[issue_id]}

    # ------------------------------------------------------------ timers
    def tick(self) -> list[dict]:
        """Everything due by the engine clock: expiry, backup approver, no-shows, retraction, auto-escalation."""
        with self.lock:
            now = self.now()
            an = self.analysis(now)
            open_gaps = {g.id for g in an.all_gaps}
            started = {}
            for e in self.ds.events:
                if e.event_type == "shift_started" and e.event_time <= now and e.ingested_at <= now:
                    started[e.physician_id] = e.event_time
            changes = self.book.tick(now, open_gaps, started)
            ack_after = float(self.cfg.get_path("alerts.coordinator_ack_minutes", 3))
            for issue in an.issues:
                if issue.severity != "critical" or issue.id in self.acks or issue.id in self.alerted:
                    continue
                first_seen = self.first_seen.setdefault(issue.id, now)
                if minutes_between(first_seen, now) >= ack_after:
                    self.alerted.add(issue.id)
                    self.notifier.internal(f"Not acknowledged in {ack_after:g} min: {issue.what}",
                                           audience="clinical_lead")
                    changes.append(self.book.log(now, "system", "auto_escalated", issue_id=issue.id))
            stale_after = float(self.cfg.get_path("alerts.stale_data_minutes", 10))
            if an.world.last_ingested_at and minutes_between(an.world.last_ingested_at, now) >= stale_after:
                key = f"stale-{iso(an.world.last_ingested_at)}"
                if key not in self.alerted:
                    self.alerted.add(key)
                    self.notifier.internal(f"No new records since {fmt_clock(an.world.last_ingested_at)}.",
                                           audience="coordinator")
                    changes.append(self.book.log(now, "system", "stale_data_alert",
                                                 last_record=iso(an.world.last_ingested_at)))
            age = minutes_between(an.world.last_ingested_at, now) if an.world.last_ingested_at else 0
            publish({"DataAgeMinutes": age,
                     "OpenCriticalIssues": sum(1 for i in an.issues if i.severity == "critical"),
                     "OffersSent": sum(1 for o in self.book.offers.values() if o.sent_at),
                     "OffersAccepted": sum(1 for o in self.book.offers.values() if o.status == "accepted"),
                     "InternalAlerts": sum(1 for o in self.notifier.outbox if o["kind"] == "internal")})
            if changes:
                self._save()
            return changes

    def _save_processed(self) -> None:
        """AWS: write the normalized, deduplicated, event-time-ordered log the engine rebuilt from."""
        if not hasattr(self.storage, "save_processed"):
            return
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(EVENT_COLUMNS + ["source"])
        for e in self.ds.events:
            writer.writerow([e.event_id, e.event_time.isoformat(), e.ingested_at.isoformat(), e.consult_id,
                             e.event_type, e.physician_id, e.hospital_id, e.description, e.source])
        self.storage.save_processed(buf.getvalue())

    def _summary(self, plan: dict, issues: list[dict]) -> dict:
        linked = [i for i in issues if i.get("plan_id") == plan["id"]] or [
            i for i in issues if set(plan["gap_ids"]) & {i["id"].replace("iss-", "", 1)}]
        rows = (plan.get("eligibility") or {}).get("rows") or []
        best = plan["candidates"][0]
        problem = linked[0]["what"].rstrip(".") if linked else "coverage is short at " + ", ".join(plan["hospital_names"])
        fields = {"kind": plan["kind"], "doctor": best["name"], "hospitals": ", ".join(best["hospitals"]),
                  "window": best["window_label"], "checked": len(rows),
                  "eligible": sum(1 for r in rows if r.get("eligible")) or 1,
                  "scheduled": best["window_label"].split(" to ")[-1], "problem": problem[0].lower() + problem[1:]}
        return plan_summary(fields)

    def scoreboard(self) -> dict:
        with self.lock:
            key = (len(self.ds.events), self.now())
            if getattr(self, "_score_cache", (None,))[0] != key:
                self._score_cache = (key, scoreboard(self.ds, self.now()))
            return self._score_cache[1]

    def insights(self) -> dict:
        with self.lock:
            an = self.analysis()
            return {"time_to_doctor": time_to_doctor(self.ds, an.world),
                    "credentialing": credentialing_impact(self.ds, an), "handoffs": handoffs(self.ds, an)}

    def voice(self, offer_id: str) -> bytes | None:
        return speak(self.book.offers[offer_id].message)

    # ------------------------------------------------------------ extras
    def simulate(self, gap_id: str | None = None) -> dict:
        with self.lock:
            an = self.analysis()
            joins = list(self.book.joins())
            if not any(j.kind == "split_shift" for j in joins):
                result = an.gap_eligibility.get(gap_id) if gap_id else an.cluster
                if result and result.best:
                    o = result.best.offer
                    limit = self.cfg.get_path("flood_guard.limit_to_gap_hospitals", True)
                    joins.append(PlannedJoin(result.best.physician_id, o.online_at, o.end,
                                             set(o.hospitals) if limit else None, "recommended offer"))
            sim_world = replay(self.ds, an.world.now, [j for j in joins if j.label != "recommended offer"])
            result = simulate_flood_guard(self.ds, sim_world, an, joins)
            new = [j for j in joins if j.label == "recommended offer"]
            if new:
                base = simulate_flood_guard(self.ds, sim_world, an, [])
                j = new[0]
                hours = round((j.end - j.online_at).total_seconds() / 3600, 1)
                w0, w1 = base["average_wait_minutes"]["with"], result["average_wait_minutes"]["with"]
                u0, u1 = base["unrouted"]["with"], result["unrouted"]["with"]
                result["value_of_acting"] = {
                    "physician_hours": hours, "wait_without_offer": w0, "wait_with_offer": w1,
                    "unrouted_without_offer": u0, "unrouted_with_offer": u1,
                    "text": (f"Cost: {hours} extra physician hours. Over the next {result['minutes']} minutes the "
                             f"average wait for a doctor goes from {w0} to {w1} min, and calls nobody can take from "
                             f"{u0} to {u1} (simulated estimate)."),
                    "note": ("Research average, illustrative only: each 15 minutes faster to stroke treatment is linked "
                             "to about 4% better odds of a good outcome. This compares acting with not acting; it never "
                             "compares doctors by cost."),
                }
            return result

    def audit(self) -> list[dict]:
        return list(self.book.audit)

    def issue_detail(self, issue_id: str) -> dict:
        with self.lock:
            an = self.analysis()
            issue = next((i for i in an.issues if i.id == issue_id), None)
            if issue is None:
                raise KeyError(issue_id)
            names = {h.id: h.name for h in self.ds.ref.hospitals.values()}
            out = issue.to_dict()
            hid = issue.where.get("hospital_id")
            if hid:
                out["strip"] = an.projection["hospitals"].get(hid)
                out["hospital"] = {"next_up": an.next_up[hid].to_dict(), "coverage": an.coverage_now[hid]}
            primary = issue.plan.primary if issue.plan else None
            if primary and primary.kind in ("split_shift", "covered_by_offer"):
                gap_id = issue.id.replace("iss-", "", 1)
                result = an.gap_eligibility.get(gap_id) or an.cluster
                out["eligibility"] = result.to_dict(names) if result else None
            elif issue.type == "coverage_gap_projected":
                out["eligibility"] = an.gap_eligibility[issue.id.replace("iss-", "", 1)].to_dict(names)
            elif an.cluster:
                out["eligibility"] = an.cluster.to_dict(names)
            return out
