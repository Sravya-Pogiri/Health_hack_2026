import { useState } from "react";
import { post } from "../api";
import { fmtTime, minutesBetween, mmss, shortHospital } from "../util";
import { EligibilityTable } from "./IssueDrawer";
import Card from "./Card";

// The clinical lead approves or rejects split-shift offers and authorizes escalations.
// The page answers three questions in order: what needs my decision, where is stroke care short,
// and what is already moving.

const flat = (state: any) => state.issues.flatMap((g: any) => g.issues);
const BY = "Clinical lead on duty";

export default function LeadView({ state, act }: any) {
  const issues = flat(state);
  const pending = state.plans.filter((p: any) => p.status === "pending_approval");
  const decided = state.plans.filter((p: any) => p.status !== "pending_approval");
  const escalations = state.coordinator_actions.filter(
    (a: any) => a.kind === "escalate" && a.status !== "undone" && !(a.issue_id && issues.find((i: any) => i.id === a.issue_id)?.acknowledged_at));
  const decisions = pending.length + escalations.length;

  const strokeRows = issues.filter((i: any) => i.stroke && i.severity === "critical");

  return (
    <div className="overview">
      <Pulse state={state} issues={issues} />

      <div className="ov-grid">
        <Card title="Needs your decision" count={decisions} sub="Approve or reject in one step. Every decision is logged with your name.">
          <div className="card-body stack">
            {pending.map((p: any) => <Approval key={p.id} plan={p} state={state} act={act} issues={issues} />)}
            {escalations.map((a: any) => <Escalation key={a.id} a={a} issues={issues} act={act} />)}
            {decisions === 0 && <NothingYet issues={issues} />}
          </div>
        </Card>
        <Card title="Stroke care at risk now" count={strokeRows.length} sub="Stroke consults past their time limit. Click one for the reason.">
          <StrokeRisk rows={strokeRows} />
        </Card>
      </div>

      <Card title="Coverage outlook" sub="Hospitals that go short in the next few hours. On shift / required, every 15 minutes.">
        <CoverageGrid state={state} />
      </Card>

      {decided.length > 0 && (
        <Card title="Your decisions" count={decided.length} sub="Offers you approved or rejected, and what happened next">
          <Decided plans={decided} />
        </Card>
      )}
    </div>
  );
}

function Pulse({ state, issues }: any) {
  const strokeNow = issues.filter((i: any) => i.stroke && i.severity === "critical").length;
  const noneFree = state.hospitals.filter((h: any) => h.free_now === 0 && h.waiting_consults?.length).length;
  const later = state.gaps.filter((g: any) => g.kind !== "now").length;
  const joining = state.joins.length;
  const tiles = [
    { n: strokeNow, label: `Stroke item${strokeNow === 1 ? "" : "s"} past the limit`, tone: strokeNow ? "critical" : "ok" },
    { n: noneFree, label: `Hospital${noneFree === 1 ? "" : "s"} with nobody free`, tone: noneFree ? "critical" : "ok" },
    { n: later, label: `Coverage gap${later === 1 ? "" : "s"} coming up`, tone: later ? "projected" : "ok" },
    { n: joining, label: `Doctor${joining === 1 ? "" : "s"} joining`, tone: joining ? "ok" : "info" },
  ];
  return (
    <div className="pulse">
      {tiles.map((t) => (
        <div key={t.label} className={`pulse-tile ${t.tone}`}>
          <span className="n">{t.n}</span>
          <span className="l">{t.label}</span>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ decisions

function Approval({ plan, state, act, issues }: any) {
  const [showTable, setShowTable] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [showAlts, setShowAlts] = useState(false);
  const best = plan.candidates[0];
  const sms = !!state.messaging?.sms;
  const rows = plan.eligibility?.rows || [];
  const eligible = rows.filter((r: any) => r.eligible).length;
  const linked = issues.filter((i: any) => i.plan_id === plan.id);
  const gapIssues = issues.filter((i: any) => plan.gap_ids.includes(i.id.replace("iss-", "")));
  const reasons = [...linked, ...gapIssues.filter((g: any) => !linked.includes(g))].slice(0, 4);
  const waited = minutesBetween(plan.proposed_at, state.now);
  const [start, end] = best.window_label.replace(" ET", "").split(" to ");
  const isEarly = plan.kind === "start_early";
  // The reasoning: why this doctor (fatigue and fairness rules, with numbers) and why not a smaller fix.
  const chosen = rows.find((r: any) => r.physician_id === best.physician_id);
  const checks = (chosen?.rules || []).filter((r: any) => ["rest", "week_hours", "weekly_offers", "no_overlap"].includes(r.key));
  const alts = (reasons.find((i: any) => i.what_to_do?.alternatives?.length)?.what_to_do.alternatives || [])
    .filter((a: any) => a.why_not && a.kind !== "incoming_join");

  return (
    <div className="approval lead-approval">
      <div className="lead-approval-top">
        <span className="chip accent">{isEarly ? "Early start" : "Split-shift offer"}</span>
        {reasons.some((i: any) => i.stroke) && <span className="chip stroke">Stroke risk</span>}
        {plan.backup_escalated_at && <span className="chip warning">Sent to backup lead at {fmtTime(plan.backup_escalated_at)}</span>}
        <span className="muted small">Proposed by the coordinator {waited > 0 ? `${waited} min ago` : "just now"}</span>
      </div>

      <h3>{isEarly ? `Let ${best.name} start at ${start}` : `Offer ${best.name} ${start} to ${end}`}</h3>

      <div className="facts">
        <div><span>Covers</span><b>{best.hospitals.map(shortHospital).join(", ")}</b></div>
        <div><span>Online in</span><b>{best.minutes_to_online} min</b></div>
        <div><span>{isEarly ? "Starts early by" : "Shift length"}</span><b>{hoursLabel(plan, best)}</b></div>
        {!isEarly && rows.length > 0 && (
          <div>
            <span>Eligibility</span>
            <b>
              {eligible} of {rows.length} qualify{" "}
              <button className="btn ghost small" onClick={() => setShowTable(!showTable)}>
                {showTable ? "Hide" : "See all"}
              </button>
            </b>
          </div>
        )}
      </div>

      {reasons.length > 0 && (
        <div className="why-now">
          <div className="muted small">What this fixes</div>
          <ul>
            {reasons.map((i: any) => (
              <li key={i.id}>
                <b>{i.where?.hospital_name ? shortHospital(i.where.hospital_name) : "Network"}</b>: {lower(i.title)}
                {i.severity === "projected" && <span className="muted"> (coming up)</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {checks.length > 0 && (
        <div className="why-now">
          <div className="muted small">Why {best.name}</div>
          <ul className="checks">
            {checks.map((r: any) => <li key={r.key}><span className="pass">Pass</span> {r.reason}</li>)}
          </ul>
        </div>
      )}

      {alts.length > 0 && (
        <div className="why-now">
          <button className="btn ghost small alt-toggle" onClick={() => setShowAlts(!showAlts)} aria-expanded={showAlts}>
            {showAlts ? "Hide" : "Why not a smaller fix?"} ({alts.length} ruled out)
          </button>
          {showAlts && (
            <ul>
              {alts.map((a: any) => <li key={a.kind}><b>{ALT_LABEL[a.kind] || a.label}</b>: {a.why_not}</li>)}
            </ul>
          )}
        </div>
      )}

      {showTable && <EligibilityTable e={plan.eligibility} />}

      <div className="muted small">
        {best.name} gets hospital names, hours and start time only. No patient details.
        {sms && ` It goes out as a text message, and ${best.name} replies 1 to accept or 2 to decline.`}
        {plan.candidates.length > 1 && ` If they decline or don't answer in 10 minutes, it goes to ${plan.candidates[1].name}.`}
      </div>

      {!rejecting ? (
        <div className="buttons">
          <button className="btn primary big" onClick={() => act(() => post(`/api/offers/${plan.id}/approve`, { by: BY }), sms ? `Approved. Texting ${best.name}.` : `Approved. Offer sent to ${best.name}.`)}>
            {sms ? "Approve and send text" : "Approve and send"}
          </button>
          <button className="btn big" onClick={() => setRejecting(true)}>Reject</button>
        </div>
      ) : (
        <RejectForm planId={plan.id} act={act} onCancel={() => setRejecting(false)} />
      )}
    </div>
  );
}

const ALT_LABEL: Record<string, string> = {
  reassign: "Reassign to a doctor on shift",
  wait: "Wait for the assigned doctor",
  interrupt: "Pull someone off lower-priority work",
  start_early: "Ask a scheduled doctor to start early",
  split_shift: "Split-shift offer",
};

const REJECT_REASONS = ["Not needed yet", "Prefer a different fix", "Handle by escalation instead"];

function RejectForm({ planId, act, onCancel }: any) {
  const [reason, setReason] = useState<string>(REJECT_REASONS[0]);
  const [note, setNote] = useState("");
  return (
    <div className="reject-form">
      <div className="muted small">Why are you rejecting it? This goes in the audit log.</div>
      <div className="reason-row">
        {REJECT_REASONS.map((r) => (
          <button key={r} className={`btn small ${reason === r ? "selected" : ""}`} onClick={() => setReason(r)}>{r}</button>
        ))}
      </div>
      <input id={`reject-note-${planId}`} className="i" placeholder="Add a note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="buttons">
        <button className="btn danger" onClick={() => act(() => post(`/api/offers/${planId}/reject`, { by: BY, note: note ? `${reason}: ${note}` : reason }), "Rejected. The coordinator has been told.")}>
          Confirm reject
        </button>
        <button className="btn" onClick={onCancel}>Back</button>
      </div>
    </div>
  );
}

function Escalation({ a, issues, act }: any) {
  const issue = issues.find((i: any) => i.id === a.issue_id);
  return (
    <div className="approval lead-approval">
      <div className="lead-approval-top">
        <span className="chip critical">Escalated to you</span>
        {issue?.stroke && <span className="chip stroke">Stroke risk</span>}
        <span className="muted small">{fmtTime(a.at)} by the coordinator</span>
      </div>
      <h3>{issue ? `${issue.where?.hospital_name ? shortHospital(issue.where.hospital_name) + ": " : ""}${issue.title}` : "Escalation"}</h3>
      <p className="esc-text">{a.summary}</p>
      {issue?.what_to_do?.primary?.why && <div className="muted small">Relay: {issue.what_to_do.primary.why}</div>}
      <div className="buttons">
        <button className="btn primary" onClick={() => act(() => post(`/api/issues/${a.issue_id}/ack`, { by: BY }), "Taken. Logged with your name.")}>
          I'm handling it
        </button>
      </div>
    </div>
  );
}

function NothingYet({ issues }: any) {
  // Fixes that will need the lead's approval once the coordinator proposes them.
  const coming = issues.filter((i: any) => i.what_to_do?.primary?.approver === "clinical_lead" && !i.plan_id
    && ["split_shift", "start_early", "escalate_lead"].includes(i.what_to_do.primary.kind));
  return (
    <div className="panel quiet">
      <b>Nothing needs your decision right now.</b>
      {coming.length > 0 ? (
        <>
          <span className="muted"> These will come to you once the coordinator proposes them:</span>
          <ul>
            {coming.slice(0, 4).map((i: any) => (
              <li key={i.id}>
                <b>{i.where?.hospital_name ? shortHospital(i.where.hospital_name) : "Network"}</b>:{" "}
                {i.what_to_do.primary.kind === "escalate_lead" ? `${lower(i.title)}, and no off-duty doctor qualifies, so it will be escalated to you` : i.what_to_do.primary.label}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <span className="muted"> Offers and escalations will appear here.</span>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ coverage

function CoverageGrid({ state }: any) {
  const times: string[] = state.projection.times;
  const all = state.hospitals.map((h: any) => ({ h, pts: state.projection.hospitals[h.id] || [] }));
  // Keep hospitals an approved doctor is joining for, so the filled gap shows up green.
  const joinedFor = new Set(state.joins.flatMap((j: any) => j.hospitals || []));
  const short = all.filter((x: any) => x.pts.some((p: any) => p.short > 0) || joinedFor.has(x.h.id))
    .sort((a: any, b: any) => firstShort(a.pts) - firstShort(b.pts));
  const fine = all.length - short.length;
  const joiners = new Set(state.joins.map((j: any) => j.physician_id));
  const names: Record<string, string> = {};
  state.physicians.forEach((p: any) => (names[p.id] = p.name));

  if (short.length === 0) {
    return <div className="panel quiet"><b>Every hospital is covered</b> <span className="muted">through {fmtTime(times[times.length - 1])}.</span></div>;
  }
  return (
    <div className="panel cov">
      <div className="cov-scroll">
        <table className="cov-grid">
          <thead>
            <tr>
              <th />
              {times.map((t, i) => <th key={t}>{i === 0 ? "Now" : fmtTime(t).replace(" PM", "").replace(" AM", "")}</th>)}
            </tr>
          </thead>
          <tbody>
            {short.map(({ h, pts }: any) => (
              <tr key={h.id}>
                <th>{shortHospital(h.name)}{h.surge?.active && <span className="chip warning tiny">Surge</span>}</th>
                {pts.map((p: any) => {
                  const helped = p.staffed_ids.some((id: string) => joiners.has(id)) && joinedFor.has(h.id);
                  const cls = p.short > 0 ? "short" : helped ? "helped" : "ok";
                  return (
                    <td key={p.t} className={cls}
                      title={`${fmtTime(p.t)}: ${p.staffed} of ${p.required} needed. ${p.staffed_ids.map((i: string) => names[i] || i).join(", ") || "Nobody"}`}>
                      {p.staffed}/{p.required}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="cov-legend muted small">
        <span><i className="sw short" />Short</span>
        <span><i className="sw helped" />Covered by an approved offer</span>
        <span><i className="sw ok" />Covered</span>
        <span>Cells show credentialed doctors on shift / needed. {fine} other hospital{fine === 1 ? " is" : "s are"} covered the whole time.</span>
      </div>
    </div>
  );
}

const firstShort = (pts: any[]) => { const i = pts.findIndex((p) => p.short > 0); return i < 0 ? 99 : i; };

// ------------------------------------------------------------------ stroke risk

function StrokeRisk({ rows }: any) {
  const [open, setOpen] = useState<string | null>(null);
  if (rows.length === 0) return <div className="panel quiet"><b>No stroke consult is past its limit.</b></div>;
  return (
    <div className="panel risk">
      {rows.map((i: any) => {
        const p = i.what_to_do?.primary;
        const yours = p?.approver === "clinical_lead";
        const covered = p?.kind === "incoming_join" && p?.viable;
        return (
          <div key={i.id} className="risk-row">
            <button className="risk-main" onClick={() => setOpen(open === i.id ? null : i.id)} aria-expanded={open === i.id}>
              <span className="iv-dot critical" />
              <span className="risk-where">{i.where?.hospital_name ? shortHospital(i.where.hospital_name) : i.where?.physician_name}</span>
              <span className="risk-what">{i.title}</span>
              <span className="risk-time">{i.wait_minutes ? `${i.wait_minutes} min` : ""}</span>
              <span className={`risk-owner ${covered ? "covered" : yours ? "yours" : ""}`}>
                {covered ? "Help on the way" : yours ? "Needs your approval" : "Coordinator"}
              </span>
            </button>
            {open === i.id && (
              <div className="risk-detail">
                <div>{i.why}</div>
                {p && <div className="muted">Fix: {p.label}{p.approver_label ? ` (${p.approver_label})` : ""}</div>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ decided

function Decided({ plans }: any) {
  return (
    <div className="panel risk">
      {plans.map((p: any) => {
        const last = p.offers?.[p.offers.length - 1];
        const s = decidedStatus(p, last);
        return (
          <div key={p.id} className="risk-row static">
            <span className={`chip ${s.tone}`}>{s.chip}</span>
            <span className="risk-what">{p.candidates[0]?.name}, {p.candidates[0]?.window_label}</span>
            <span className="muted small">{s.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function decidedStatus(p: any, offer: any) {
  if (p.status === "rejected") return { chip: "Rejected", tone: "info", text: p.decision_note || "" };
  if (p.status === "filled") return { chip: "Filled", tone: "ok", text: "Accepted. Coverage updated." };
  if (p.status === "exhausted") return { chip: "No taker", tone: "critical", text: "Nobody accepted. Escalate." };
  if (offer?.status === "sent" && offer.delivery && ["failed", "undelivered", "no_phone"].includes(offer.delivery.status)) {
    return { chip: "Not delivered", tone: "critical", text: `${deliveryNote(offer)}. Reach ${offer.physician_name} another way; the offer is still open in the app for ${mmss(offer.seconds_left)}.` };
  }
  if (offer?.status === "sent") {
    const note = deliveryNote(offer);
    return { chip: "Waiting", tone: "warning", text: `${note ? `${note}. ` : "Offer sent. "}Waiting for ${offer.physician_name} to reply, ${mmss(offer.seconds_left)} left` };
  }
  if (offer) return { chip: "Approved", tone: "accent", text: `Offer to ${offer.physician_name}: ${offer.status}` };
  return { chip: "Approved", tone: "accent", text: "" };
}

// ------------------------------------------------------------------ helpers

// How the text message went out, in a few words. Empty when nothing real was sent (simulated mode).
function deliveryNote(o: any): string {
  const d = o?.delivery;
  if (!d || !d.status || d.status === "simulated") return "";
  if (d.status === "no_phone") return "No phone number on file";
  if (d.status === "failed" || d.status === "undelivered") return `Text not delivered${d.error ? ` (${d.error})` : ""}`;
  if (d.channel !== "sms") return "";
  return d.status === "delivered" ? `Text delivered to ${d.to}` : `Text sent to ${d.to}`;
}

function hoursLabel(plan: any, best: any): string {
  const offer = plan.offers?.[0];
  let mins = 0;
  if (offer) mins = minutesBetween(offer.start, offer.end);
  else {
    const [a, b] = best.window_label.replace(" ET", "").split(" to ");
    mins = clockMinutes(b) - clockMinutes(a);
    if (mins < 0) mins += 24 * 60;
  }
  const h = Math.floor(mins / 60), m = mins % 60;
  return h ? `${h} h${m ? ` ${m} min` : ""}` : `${m} min`;
}

function clockMinutes(label: string): number {
  const m = label.trim().match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!m) return 0;
  let h = Number(m[1]) % 12;
  if (m[3].toUpperCase() === "PM") h += 12;
  return h * 60 + Number(m[2]);
}

const lower = (t: string) => (t ? t[0].toLowerCase() + t.slice(1) : t);
