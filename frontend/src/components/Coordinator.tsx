import { useState } from "react";
import { post } from "../api";
import { fmtTime, SEVERITY_LABEL, shortHospital } from "../util";
import IssueDrawer from "./IssueDrawer";

export default function Coordinator({ state, act }: any) {
  const [openIssue, setOpenIssue] = useState<string | null>(null);
  const dh = state.data_health;
  return (
    <>
      {dh.stale && (
        <div className="banner stale">
          No new records for {dh.last_record_minutes_ago} minutes (last at {fmtTime(dh.data_as_of)} ET). Busy doctors may
          have freed up or new consults may be waiting. Check the Synapse feed before acting.
        </div>
      )}
      <HospitalStrip state={state} />
      <div className="grid">
        <div>
          <div className="section-title">
            What to act on first ({state.summary.critical} critical, {state.summary.warning} warning,{" "}
            {state.summary.projected} coming up)
          </div>
          <div className="queue">
            {state.issues.map((g: any) => (
              <GroupCard key={g.id} group={g} state={state} act={act} open={setOpenIssue} />
            ))}
            {state.issues.length === 0 && <div className="panel empty">Nothing needs attention right now.</div>}
          </div>
        </div>
        <PhysicianList state={state} />
      </div>
      {openIssue && <IssueDrawer issueId={openIssue} state={state} act={act} onClose={() => setOpenIssue(null)} />}
    </>
  );
}

function HospitalStrip({ state }: any) {
  const gapsBy: Record<string, any[]> = {};
  for (const g of state.gaps) (gapsBy[g.hospital_id] ||= []).push(g);
  return (
    <>
      <div className="section-title">Hospital coverage now (staffed / required, free to take a call)</div>
      <div className="hospitals">
        {state.hospitals.map((h: any) => {
          const later = (gapsBy[h.id] || []).find((g: any) => g.kind === "projected");
          const short = h.staffed_now < h.required_now || h.promise_at_risk;
          return (
            <div key={h.id} className={`hosp ${short ? "short" : ""}`} title={h.next_change || h.name}>
              <div className="name">{shortHospital(h.name)}</div>
              {h.surge.active && <div><span className="chip warning" title={`${h.surge.trailing_count} consults in the last hour; normal busy level is ${h.surge.p90}`}>Surge {h.surge.trailing_count} vs {h.surge.p90}</span></div>}
              <div className="nums">
                <span><b>{h.staffed_now}/{h.required_now}</b> staffed</span>
                <span><b>{h.free_now}</b> free</span>
              </div>
              {h.promise_at_risk ? (
                <div className="risk" title="Sevaro OneCall: connect to a neurologist in under 45 seconds">Nobody free: 45-second promise at risk</div>
              ) : (
                <div className="muted">Next up: {h.next_up?.name?.split(" ").slice(-1)[0]}</div>
              )}
              {later && (
                <div className="later">
                  Short from {fmtTime(later.start)} ({later.staffed}/{later.required})
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

export function actionFor(opt: any, issue: any) {
  // Map a recommended option to the API call that carries it out.
  const where = issue.where || {};
  switch (opt.kind) {
    case "split_shift":
    case "start_early":
    case "covered_by_offer":
      return { label: "Propose to clinical lead", call: () => post("/api/offers/propose", { issue_id: issue.id }), done: "Sent to the clinical lead for approval" };
    case "reassign":
      return { label: "Reassign", call: () => post("/api/actions/reassign", { issue_id: issue.id, consult_id: where.consult_id, physician_id: opt.physician_id }), done: "Reassignment requested" };
    case "verify":
    case "interrupt":
      return { label: "Send request", call: () => post("/api/actions/contact", { issue_id: issue.id, physician_id: opt.physician_id, summary: opt.label }), done: "Request sent to the doctor" };
    case "escalate_lead":
      return { label: "Escalate", call: () => post("/api/actions/escalate", { issue_id: issue.id, summary: issue.what }), done: "Escalated to the clinical lead" };
    case "escalate_hospital":
      return { label: "Log hospital call", call: () => post("/api/actions/contact", { issue_id: issue.id, target: "hospital", hospital_id: where.hospital_id, summary: opt.label }), done: "Hospital call logged" };
    case "wind_down":
    case "protect_note_time":
      return { label: "Turn on", call: () => post("/api/actions/contact", { issue_id: issue.id, physician_id: opt.physician_id, summary: opt.label }), done: "Wind-down noted" };
    case "wait":
    case "incoming_join":
      return { label: "OK, keep watching", ack: true, call: () => post(`/api/issues/${issue.id}/ack`), done: "Acknowledged; Relay keeps watching" };
    default:
      return { label: "Acknowledge", ack: true, call: () => post(`/api/issues/${issue.id}/ack`), done: "Acknowledged" };
  }
}

function GroupCard({ group, state, act, open }: any) {
  const [showRelated, setShowRelated] = useState(false);
  const issue = group.issues.find((i: any) => i.id === group.primary_issue_id) || group.issues[0];
  const plan = issue.what_to_do;
  const primary = plan?.primary;
  const action = primary ? actionFor(primary, issue) : null;
  const linkedPlan = issue.plan_id ? state.plans.find((p: any) => p.id === issue.plan_id) : null;
  const others = group.issues.filter((i: any) => i.id !== issue.id);
  const where = issue.where || {};
  const whereText = [where.hospital_name, where.consult_id, where.physician_name].filter(Boolean).join(" · ");

  return (
    <div className={`card ${group.severity}`}>
      <div className="card-top">
        <span className="rank">{group.rank}</span>
        <span className="card-title">{group.title}</span>
        {group.stroke && <span className="chip stroke">Stroke risk</span>}
        <span className={`chip ${group.severity}`}>{SEVERITY_LABEL[group.severity]}</span>
        {issue.acknowledged_at && <span className="chip ok">Acknowledged</span>}
      </div>
      <dl className="q">
        <dt>What is going on</dt>
        <dd>{issue.what}</dd>
        <dt>Where</dt>
        <dd>{whereText || "Network-wide"}</dd>
        <dt>Why it matters</dt>
        <dd>{issue.why}</dd>
      </dl>
      {primary && (
        <div className="todo">
          <div className="label">What to do: {primary.label}</div>
          <div className="meta">
            {primary.approver_label}
            {primary.eta_minutes ? ` · help in about ${primary.eta_minutes} min` : ""}
            {primary.why ? ` · ${primary.why}` : ""}
          </div>
          {primary.warnings?.length > 0 && <div className="warn-text">{primary.warnings.join(" ")}</div>}
          {plan.also_now?.length > 0 && (
            <ul>
              {plan.also_now.map((o: any, i: number) => (
                <li key={i}>
                  Also now: {o.label}{" "}
                  <button className="btn ghost small" onClick={() => { const a = actionFor(o, issue); act(a.call, a.done); }}>
                    {actionFor(o, issue).label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="card-actions">
        {linkedPlan ? (
          <span className="chip accent">
            Plan {linkedPlan.id}: {planStatus(linkedPlan)}
          </span>
        ) : (
          action && (
            <button className="btn primary" onClick={() => act(action.call, action.done)}>
              {action.label}
            </button>
          )
        )}
        <button className="btn" onClick={() => open(issue.id)}>Details and why not</button>
        {!issue.acknowledged_at && !(action as any)?.ack && (
          <button className="btn ghost" onClick={() => act(() => post(`/api/issues/${issue.id}/ack`), "Acknowledged")}>
            Acknowledge
          </button>
        )}
      </div>
      {others.length > 0 && (
        <div className="related">
          <button onClick={() => setShowRelated(!showRelated)}>
            {showRelated ? "Hide" : "Show"} {others.length} related item{others.length > 1 ? "s" : ""}
          </button>
          {showRelated && (
            <div className="related-list">
              {others.map((o: any) => (
                <div key={o.id} className="related-item">
                  <span className={`chip ${o.severity}`}>{SEVERITY_LABEL[o.severity]}</span>
                  <span style={{ flex: 1 }}>{o.what}</span>
                  <button className="btn ghost small" onClick={() => open(o.id)}>Details</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function planStatus(p: any): string {
  const offer = p.offers?.[p.offers.length - 1];
  if (p.status === "pending_approval") return "waiting for clinical lead approval";
  if (p.status === "approved" && offer) return `offer to ${offer.physician_name} ${offer.status}`;
  if (p.status === "filled") return `filled by ${p.offers.find((o: any) => o.status === "accepted")?.physician_name || "a doctor"}`;
  if (p.status === "exhausted") return "nobody accepted; escalate";
  return p.status;
}

function verdictClass(code: string) {
  if (["available", "in_meeting"].includes(code)) return "ok";
  if (["busy", "unavailable"].includes(code)) return "busy";
  if (code === "off_shift") return "off";
  return "flag";
}

function PhysicianList({ state }: any) {
  const docs = state.physicians.filter((p: any) => p.on_shift || state.joins.some((j: any) => j.physician_id === p.id));
  const off = state.physicians.filter((p: any) => !docs.includes(p));
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Doctors</h2>
        <span className="sub">true availability, not self-reported status</span>
      </div>
      <div className="docs">
        {docs.map((p: any) => (
          <div key={p.id} className="doc" title={p.verdict.reason}>
            <div className="row1">
              <span className="nm">{p.name}</span>
              <span className={`verdict ${verdictClass(p.verdict.code)}`}>{p.verdict.label}</span>
            </div>
            <div className="act">{p.activity_text}</div>
            <div className="row3">
              {p.free_in_minutes > 0 && <span>Free in about {p.free_in_minutes} min</span>}
              {p.long_case && <span>Long case</span>}
              {p.minutes_to_shift_end !== null && <span>Ends in {p.minutes_to_shift_end} min</span>}
              <span>{p.consults_this_shift} consults</span>
              {p.open_consults.length > 0 && <span>{p.open_consults.length} open</span>}
              {p.self_reported_status !== "Available" && <span>Says: {p.self_reported_status}</span>}
              {p.desktop_agent === "Offline" && <span style={{ color: "var(--warning)" }}>Agent offline</span>}
            </div>
            {p.load && (
              <div className="loadbar" title={`Load: ${p.load.level} (soft limit ${p.load.soft_limit})`}>
                <span className={p.load.consults >= p.load.soft_limit ? "high" : ""}
                  style={{ width: `${Math.min(100, (100 * p.load.consults) / p.load.soft_limit)}%` }} />
              </div>
            )}
          </div>
        ))}
        <div className="doc muted small">
          Off shift: {off.map((p: any) => p.name.replace("Dr. ", "")).join(", ")}
        </div>
      </div>
    </div>
  );
}
