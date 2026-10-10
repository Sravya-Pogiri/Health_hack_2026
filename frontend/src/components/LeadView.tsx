import { post } from "../api";
import { fmtTime, shortHospital } from "../util";
import { planStatus } from "./Coordinator";
import { EligibilityTable } from "./IssueDrawer";
import { useState } from "react";

export default function LeadView({ state, act }: any) {
  const pending = state.plans.filter((p: any) => p.status === "pending_approval");
  const active = state.plans.filter((p: any) => p.status !== "pending_approval");
  const strokeRisk = state.issues
    .flatMap((g: any) => g.issues)
    .filter((i: any) => i.stroke && (i.severity === "critical" || i.type === "coverage_gap_projected"));
  const escalations = state.coordinator_actions.filter((a: any) => a.kind === "escalate" && a.status !== "undone");

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto" }}>
      <div className="section-title">Waiting for your approval ({pending.length})</div>
      {pending.length === 0 && <div className="panel empty">No offers are waiting for approval.</div>}
      {pending.map((p: any) => <Approval key={p.id} plan={p} state={state} act={act} />)}

      {escalations.length > 0 && (
        <>
          <div className="section-title">Escalated to you</div>
          {escalations.map((a: any) => (
            <div key={a.id} className="approval">
              <span className="chip critical">Escalation</span> <span className="muted small">{fmtTime(a.at)} ET by {a.by}</span>
              <p style={{ marginBottom: 0 }}>{a.summary}</p>
            </div>
          ))}
        </>
      )}

      <div className="section-title">Stroke care at risk</div>
      <div className="panel">
        <table className="t">
          <tbody>
            {strokeRisk.map((i: any) => (
              <tr key={i.id}>
                <td style={{ width: 90 }}><span className={`chip ${i.severity}`}>{i.severity === "critical" ? "Critical" : "Coming up"}</span></td>
                <td><b>{i.what}</b><div className="muted small">{i.why}</div></td>
                <td style={{ width: 260 }}>{i.what_to_do?.primary?.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {active.length > 0 && (
        <>
          <div className="section-title">Decided plans</div>
          <div className="panel">
            <table className="t">
              <tbody>
                {active.map((p: any) => (
                  <tr key={p.id}>
                    <td style={{ width: 90 }}>{p.id}</td>
                    <td>{p.title}</td>
                    <td>{planStatus(p)}{p.approved_by ? ` (approved by ${p.approved_by})` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Approval({ plan, state, act }: any) {
  const [showTable, setShowTable] = useState(false);
  const issues = state.issues.flatMap((g: any) => g.issues).filter((i: any) => i.plan_id === plan.id);
  const best = plan.candidates[0];
  const rows = plan.eligibility?.rows || [];
  const checked = rows.length;
  const eligible = rows.filter((r: any) => r.eligible).length;
  const why = issues.slice(0, 3);
  return (
    <div className="approval">
      <span className="chip accent">{plan.kind === "start_early" ? "Early start request" : "Split-shift offer"}</span>{" "}
      {issues.some((i: any) => i.stroke) && <span className="chip stroke">Stroke risk</span>}
      {plan.backup_escalated_at && <span className="chip warning">Sent to backup lead at {fmtTime(plan.backup_escalated_at)}</span>}
      <h3>{plan.title}</h3>
      <div className="summary">
        <b>{best.name}</b> would cover {best.hospitals.map(shortHospital).join(", ")}, {best.window_label}, online in{" "}
        {best.minutes_to_online} min.{" "}
        {plan.kind === "split_shift"
          ? `${checked} off-duty doctors were checked against every rule; ${eligible === 1 ? "only this one qualifies" : `${eligible} qualify, ranked by speed then fairness`}. No rule was bent.`
          : `${plan.eligibility?.note || ""}`}
        {plan.candidates.length > 1 && ` If they decline or do not answer in 10 minutes, Relay offers it to ${plan.candidates.slice(1).map((c: any) => c.name).join(", then ")}.`}
      </div>
      {why.length > 0 && (
        <>
          <div className="muted small" style={{ marginTop: 8 }}>Why it is needed</div>
          <ul style={{ marginTop: 4 }}>
            {why.map((i: any) => <li key={i.id}><b>{i.what}</b> {i.why}</li>)}
          </ul>
        </>
      )}
      <div className="muted small">
        The doctor will receive only hospital names, hours, start time, and why they were asked. No patient information.
      </div>
      {plan.eligibility?.rows && (
        <button className="btn ghost small" onClick={() => setShowTable(!showTable)}>
          {showTable ? "Hide" : "Show"} every doctor considered
        </button>
      )}
      {showTable && <EligibilityTable e={plan.eligibility} />}
      <div className="buttons">
        <button className="btn primary big" onClick={() => act(() => post(`/api/offers/${plan.id}/approve`, { by: "Clinical lead on duty" }), "Approved. Offer sent.")}>
          Approve and send
        </button>
        <button className="btn big" onClick={() => {
          const note = prompt("Reason for rejecting (optional)") || "";
          act(() => post(`/api/offers/${plan.id}/reject`, { by: "Clinical lead on duty", note }), "Rejected");
        }}>
          Reject
        </button>
      </div>
    </div>
  );
}
