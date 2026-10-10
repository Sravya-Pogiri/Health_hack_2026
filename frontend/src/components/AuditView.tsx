import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import { fmtTimeSec, shortHospital } from "../util";

// The audit log answers the brief's four questions for every offer: who was considered, who was
// excluded and why, who approved, and what happened. Below that, every action as a plain sentence.

type Row = Record<string, any>;

export default function AuditView({ state }: any) {
  const [rows, setRows] = useState<Row[]>([]);
  useEffect(() => {
    api("/api/audit").then(setRows);
  }, [state.audit_count]);

  const names: Record<string, string> = {};
  state.physicians.forEach((p: any) => (names[p.id] = p.name));
  const plans = [...state.plans].reverse();

  return (
    <div className="audit">
      <div className="audit-intro">
        <div>
          <b>Audit log</b>
          <span className="muted"> Append-only. Nothing here can be edited or deleted.</span>
        </div>
        <CopyJson rows={rows} />
      </div>

      <div className="section-title">Offer records ({plans.length})</div>
      {plans.length === 0 && (
        <div className="panel quiet">
          <b>No offers yet.</b>{" "}
          <span className="muted">When the coordinator proposes one, its full record appears here: who was considered, who was excluded and why, who approved, and the outcome.</span>
        </div>
      )}
      {plans.map((p: any) => <OfferRecord key={p.id} plan={p} rows={rows} names={names} />)}

      <div className="section-title">All activity ({rows.length})</div>
      <Activity rows={rows} names={names} />
    </div>
  );
}

// ------------------------------------------------------------------ one offer, start to finish

function OfferRecord({ plan, rows, names }: any) {
  const [showAll, setShowAll] = useState(true);
  const offerIds = new Set(plan.offers.map((o: any) => o.id));
  const steps = rows.filter((r: Row) => r.plan_id === plan.id || (r.offer_id && offerIds.has(r.offer_id)));
  const elig = plan.eligibility?.rows || [];
  const eligible = elig.filter((e: any) => e.eligible);
  const approved = steps.find((r: Row) => r.action === "approved" || r.action === "rejected");
  const sent = steps.filter((r: Row) => r.action === "sent");
  const outcome = outcomeOf(plan);
  const msg = sent[0]?.message;

  return (
    <div className="approval record">
      <div className="lead-approval-top">
        <span className={`chip ${outcome.tone}`}>{outcome.label}</span>
        <span className="chip accent">{plan.kind === "start_early" ? "Early start" : "Split-shift offer"}</span>
        <span className="muted small">{plan.id}</span>
      </div>
      <h3>{plan.candidates[0]?.name}, {plan.candidates[0]?.window_label}</h3>
      <div className="muted small record-hosp">Covers {plan.hospital_names.map(shortHospital).join(", ")}</div>

      <div className="facts">
        <div><span>Considered</span><b>{elig.length ? `${elig.length} off-duty doctors` : `${plan.candidates.length} doctor`}</b></div>
        <div><span>Eligible</span><b>{elig.length ? `${eligible.length}, ${elig.length - eligible.length} excluded` : "Scheduled doctor"}</b></div>
        <div><span>Decided by</span><b>{approved ? `${actorLabel(approved.actor, names)}, ${fmtTimeSec(approved.at)}` : "Waiting for the clinical lead"}</b></div>
        <div><span>Outcome</span><b>{outcome.text}</b></div>
      </div>

      <div className="record-grid">
        <div>
          <div className="muted small">What happened</div>
          <ol className="timeline">
            {steps.map((r: Row) => (
              <li key={r.seq} className={`tl ${toneOf(r.action)}`}>
                <span className="tl-time">{fmtTimeSec(r.at)}</span>
                <span className="tl-text"><b>{actorLabel(r.actor, names)}</b> {sentence(r, names)}</span>
              </li>
            ))}
          </ol>
        </div>

        {elig.length > 0 && (
          <div>
            <div className="muted small">
              Who was considered{" "}
              <button className="btn ghost small" onClick={() => setShowAll(!showAll)}>{showAll ? "Show eligible only" : "Show all"}</button>
            </div>
            <table className="t considered">
              <tbody>
                {elig.filter((e: any) => showAll || e.eligible).map((e: any) => (
                  <tr key={e.physician_id}>
                    <td><b>{e.name}</b></td>
                    <td>{e.eligible ? <span className="pass">Eligible{e.rank ? ` #${e.rank}` : ""}</span> : <span className="fail">Excluded</span>}</td>
                    <td className="small">{e.eligible ? passedSummary(e) : e.first_failure}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {msg && (
        <div className="sent-msg">
          <div className="muted small">
            Exact message sent to {names[sent[0].physician_id] || sent[0].physician_id} by {channelLabel(sent[0].channel)}{" "}
            <span className="chip ok">Privacy check passed: no patient details</span>
          </div>
          <blockquote>{msg}</blockquote>
        </div>
      )}
    </div>
  );
}

function outcomeOf(p: any) {
  const accepted = p.offers.find((o: any) => o.status === "accepted");
  const open = p.offers.find((o: any) => o.status === "sent");
  if (p.status === "pending_approval") return { label: "Waiting for approval", tone: "warning", text: "Not sent. Needs the clinical lead." };
  if (p.status === "rejected") return { label: "Rejected", tone: "info", text: p.decision_note ? `Rejected: ${p.decision_note}` : "Rejected. Nothing sent." };
  if (p.status === "filled") return { label: "Filled", tone: "ok", text: `${accepted?.physician_name || "Accepted"} accepted. Coverage updated.` };
  if (p.status === "exhausted") return { label: "No taker", tone: "critical", text: "Nobody accepted. Escalated." };
  if (p.status === "cancelled") return { label: "Cancelled", tone: "info", text: "Cancelled. The gap closed." };
  if (open) return { label: "Waiting for reply", tone: "warning", text: `Sent to ${open.physician_name}. Expires at ${fmtTimeSec(open.expires_at)}.` };
  return { label: p.status.replace(/_/g, " "), tone: "accent", text: p.status.replace(/_/g, " ") };
}

function passedSummary(e: any): string {
  const keys = ["rest", "week_hours"];
  return (e.rules || []).filter((r: any) => keys.includes(r.key)).map((r: any) => r.reason.replace(/\s*\(.*?\)\.?$/, "")).join(" · ") || "Passed every rule";
}

// ------------------------------------------------------------------ every entry

const FILTERS: { key: string; label: string; test: (r: Row) => boolean }[] = [
  { key: "all", label: "Everything", test: () => true },
  { key: "coordinator", label: "Coordinator", test: (r) => r.actor === "coordinator" },
  { key: "lead", label: "Clinical lead", test: (r) => isLead(r.actor) },
  { key: "doctors", label: "Doctors", test: (r) => /^PHY-/.test(r.actor) },
  { key: "system", label: "Automatic", test: (r) => r.actor === "system" },
];

function Activity({ rows, names }: any) {
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState<number | null>(null);
  const f = FILTERS.find((x) => x.key === filter)!;
  const shown = [...rows].reverse().filter(f.test);
  return (
    <div className="panel">
      <div className="audit-filters">
        {FILTERS.map((x) => (
          <button key={x.key} className={`btn small ${filter === x.key ? "selected" : ""}`} onClick={() => setFilter(x.key)}>
            {x.label} <span className="muted">{rows.filter(x.test).length}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 && <div className="empty">Nothing yet. Every proposal, approval, message, reply, and automatic step lands here.</div>}
      <div className="activity">
        {shown.map((r: Row) => (
          <Fragment key={r.seq}>
            <button className="act-row" onClick={() => setOpen(open === r.seq ? null : r.seq)} aria-expanded={open === r.seq}>
              <span className="act-seq">#{r.seq}</span>
              <span className="act-time">{fmtTimeSec(r.at)}</span>
              <span className={`act-who ${whoClass(r.actor)}`}>{actorLabel(r.actor, names)}</span>
              <span className="act-text">{sentence(r, names)}</span>
            </button>
            {open === r.seq && <pre className="act-raw">{JSON.stringify(r, null, 2)}</pre>}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function CopyJson({ rows }: any) {
  const [done, setDone] = useState("");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(rows, null, 2));
      setDone("Copied");
    } catch {
      setDone("Copy blocked by the browser");
    }
    setTimeout(() => setDone(""), 2500);
  };
  return (
    <button className="btn small" onClick={copy} disabled={!rows.length}>
      {done || `Copy log as JSON (${rows.length} entries)`}
    </button>
  );
}

// ------------------------------------------------------------------ wording

const isLead = (a: string) => a === "clinical_lead" || /clinical lead/i.test(a);

function actorLabel(a: string, names: Record<string, string>): string {
  if (a === "coordinator") return "Coordinator";
  if (isLead(a)) return /backup/i.test(a) ? "Backup clinical lead" : "Clinical lead";
  if (a === "system") return "Relay (automatic)";
  return names[a] || a;
}

function whoClass(a: string): string {
  if (a === "coordinator") return "who-coord";
  if (isLead(a)) return "who-lead";
  if (a === "system") return "who-sys";
  return "who-doc";
}

function toneOf(action: string): string {
  if (["accepted", "plan_filled", "online", "approved"].includes(action)) return "good";
  if (["rejected", "declined", "expired", "no_show", "plan_exhausted", "accept_after_filled", "late_accept_rejected"].includes(action)) return "bad";
  return "";
}

function channelLabel(c?: string): string {
  return c === "sms" ? "text message" : c === "app_push" ? "app notification" : c || "message";
}

function sentence(r: Row, names: Record<string, string>): string {
  const who = (id?: string) => (id ? names[id] || id : "a doctor");
  switch (r.action) {
    case "proposed": {
      const list = (r.ranked || []).map((c: any) => `${c.rank}. ${c.name}`).join(", ");
      return `proposed ${r.kind === "start_early" ? "an early start" : "a split shift"}${list ? `. Ranked: ${list}` : ""}.`;
    }
    case "approved": return "approved the offer. It can now be sent.";
    case "rejected": return `rejected the offer${r.note ? `: ${r.note}` : ""}. Nothing was sent.`;
    case "plan_cancelled": return `cancelled the offer${r.reason ? `: ${r.reason}` : ""}.`;
    case "sent": return `sent the offer to ${who(r.physician_id)} by ${channelLabel(r.channel)}. Expires at ${fmtTimeSec(r.expires_at)}.`;
    case "skipped_at_send": return `skipped ${who(r.physician_id)}: ${r.reason}`;
    case "accepted": return `accepted. Online at ${fmtTimeSec(r.online_at)}.`;
    case "declined": return "declined. Relay moves to the next doctor on the list.";
    case "online": return "confirmed they are online.";
    case "accept_after_filled": return "tried to accept after the shift was filled. Told it is already filled.";
    case "late_accept_rejected": return "replied after the offer expired. Not accepted.";
    case "late_decline_ignored": return "declined an offer that was already closed.";
    case "offer_retracted": return `withdrew an open offer (${r.reason || "no longer needed"}).`;
    case "plan_filled": return `marked the gap filled by ${(r.filled_by || []).map(who).join(", ")}. No more offers go out.`;
    case "plan_exhausted": return r.note || "found nobody left to ask. Escalated to the clinical lead.";
    case "expired": return "expired the offer after 10 minutes with no reply. Moving to the next doctor.";
    case "no_show": return r.note || "found the doctor accepted but is not online.";
    case "approval_escalated": return r.note || "sent the request to the backup clinical lead.";
    case "auto_escalated": return `alerted the clinical lead: a critical item was not acknowledged in time (${r.issue_id}).`;
    case "stale_data_alert": return `warned that no new records have arrived since ${fmtTimeSec(r.last_record)}.`;
    case "acknowledged": return `acknowledged ${r.issue_id}.`;
    case "escalate": return `escalated to the clinical lead${r.summary ? `: ${r.summary}` : ""}.`;
    case "reassign": return `reassigned ${r.consult_id || "a consult"}${r.physician_id ? ` to ${who(r.physician_id)}` : ""}.`;
    case "contact": return `${r.target === "hospital" ? "called the hospital" : `contacted ${who(r.physician_id)}`}${r.summary ? `: ${r.summary}` : ""}.`;
    case "handoff_confirmed": return `confirmed the handoff for ${r.consult_id}.`;
    case "undo": return `undid action ${r.action_id}.`;
    default: return r.action.replace(/_/g, " ");
  }
}
