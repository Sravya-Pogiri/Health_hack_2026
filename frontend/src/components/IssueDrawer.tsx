import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import { fmtTime, SEVERITY_LABEL, shortHospital } from "../util";
import { actionFor, planStatus } from "./Coordinator";

export default function IssueDrawer({ issueId, state, act, onClose }: any) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [fg, setFg] = useState<any>(null);

  useEffect(() => {
    api(`/api/issues/${issueId}`).then(setD).catch((e) => setErr(e.message));
  }, [issueId, state.now, state.audit_count]);

  const names: Record<string, string> = {};
  state.physicians.forEach((p: any) => (names[p.id] = p.name));

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="btn close" onClick={onClose}>Close</button>
        {err && <p>This item is no longer open: {err}</p>}
        {!d && !err && <p>Loading...</p>}
        {d && (
          <>
            <span className={`chip ${d.severity}`}>{SEVERITY_LABEL[d.severity]}</span>{" "}
            {d.stroke && <span className="chip stroke">Stroke risk</span>}
            <h2>{d.title}</h2>
            <dl className="q">
              <dt>What is going on</dt><dd>{d.what}</dd>
              <dt>Where</dt>
              <dd>{[d.where.hospital_name, d.where.consult_id, d.where.physician_name].filter(Boolean).join(" · ") || "Network-wide"}</dd>
              <dt>Why it matters</dt><dd>{d.why}</dd>
            </dl>

            {d.what_to_do && <Recommendation d={d} state={state} act={act} />}

            {d.strip && <Strip points={d.strip} names={names} />}

            {d.hospital && <RouteTrace nextUp={d.hospital.next_up} names={names} />}

            {d.eligibility && <EligibilityTable e={d.eligibility} />}

            {d.eligibility && (
              <>
                <h3>Split-shift trap: where would the new doctor's calls go?</h3>
                {!fg ? (
                  <button className="btn" onClick={() => api("/api/simulate/flood-guard").then(setFg)}>
                    Run the flood guard simulation
                  </button>
                ) : (
                  <FloodGuard r={fg} />
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Recommendation({ d, state, act }: any) {
  const plan = d.what_to_do;
  const p = plan.primary;
  const action = actionFor(p, d);
  const linked = d.plan_id ? state.plans.find((x: any) => x.id === d.plan_id) : null;
  return (
    <>
      <h3>Recommended fix</h3>
      <div className="todo">
        <div className="label">{p.label}</div>
        <div className="meta">
          Who approves: {p.approver_label}
          {p.eta_minutes ? ` · help in about ${p.eta_minutes} min` : ""}
        </div>
        <div style={{ marginTop: 6 }}>Why this fix: {p.why}</div>
        {p.warnings?.map((w: string) => <div key={w} className="warn-text">{w}</div>)}
        {plan.also_now?.length > 0 && (
          <ul>{plan.also_now.map((o: any, i: number) => <li key={i}>Also now: {o.label}. {o.why}</li>)}</ul>
        )}
        {plan.backups?.length > 0 && (
          <ul>{plan.backups.map((o: any, i: number) => <li key={i}>Backup: {o.label}</li>)}</ul>
        )}
        <div className="card-actions">
          {linked ? (
            <span className="chip accent">Plan {linked.id}: {planStatus(linked)}</span>
          ) : (
            <button className="btn primary" onClick={() => act(action.call, action.done)}>{action.label}</button>
          )}
        </div>
      </div>
      {plan.alternatives?.length > 0 && (
        <>
          <h3>Why not the other options?</h3>
          <table className="t">
            <tbody>
              {plan.alternatives.map((o: any, i: number) => (
                <tr key={i}>
                  <td style={{ width: 200 }}><b>{LADDER[o.kind] || o.label}</b></td>
                  <td>{o.why_not}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

const LADDER: Record<string, string> = {
  reassign: "1. Reassign on shift",
  wait: "2. Wait for assigned doctor",
  interrupt: "3. Pull off lower-priority work",
  incoming_join: "4. Doctor already joining",
  start_early: "5. Ask to start early",
  split_shift: "6. Split-shift offer",
};

function Strip({ points, names }: any) {
  const max = Math.max(3, ...points.map((p: any) => Math.max(p.staffed, p.required)));
  return (
    <>
      <h3>Next {Math.round((new Date(points[points.length - 1].t).getTime() - new Date(points[0].t).getTime()) / 60000)} minutes: credentialed doctors on shift against what is needed</h3>
      <div className="strip">
        {points.map((p: any) => (
          <div className="slot" key={p.t} title={p.staffed_ids.map((i: string) => names[i] || i).join(", ")}>
            <span className="req">need {p.required}</span>
            <div className={`bar ${p.short ? "short" : ""}`} style={{ height: `${(80 * p.staffed) / max + 4}px` }}>{p.staffed}</div>
            <span>{fmtTime(p.t).replace(" PM", "").replace(" AM", "")}</span>
          </div>
        ))}
      </div>
      <p className="muted small">Red bars are short. Hover a bar to see who is counted.</p>
    </>
  );
}

function RouteTrace({ nextUp, names }: any) {
  return (
    <>
      <h3>If a new call came in here now (routing rules 1 to 4)</h3>
      <p>{nextUp.physician_id ? <><b>{names[nextUp.physician_id]}</b> would get it. {nextUp.reason.split(": ").slice(1).join(": ")}</> : <b>{nextUp.reason}</b>}</p>
      <table className="t">
        <tbody>
          {nextUp.steps.filter((s: any) => s.removed?.length).map((s: any) => (
            <tr key={s.rule}>
              <td style={{ width: 200 }}>Rule {s.rule}: {s.title}</td>
              <td>{s.removed.map((r: any) => <div key={r.physician_id}><b>{names[r.physician_id] || r.physician_id}</b>: {r.reason}</div>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function EligibilityTable({ e }: any) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <h3>
        Every off-duty doctor considered for {e.target_hospitals.map((h: any) => shortHospital(h.name)).join(", ")} (
        {e.eligible_count} eligible)
      </h3>
      <table className="t">
        <thead>
          <tr><th>Doctor</th><th>Result</th><th>Offer or first rule failed</th></tr>
        </thead>
        <tbody>
          {e.rows.map((r: any) => (
            <Fragment key={r.physician_id}>
              <tr className={`elig-row ${r.eligible ? "eligible" : ""}`}
                onClick={() => setOpen(open === r.physician_id ? null : r.physician_id)} style={{ cursor: "pointer" }}>
                <td><b>{r.name}</b></td>
                <td>{r.eligible ? <span className="pass">Eligible{r.rank ? ` (#${r.rank})` : ""}</span> : <span className="fail">Not eligible</span>}</td>
                <td>
                  {r.eligible
                    ? `${r.offer.label}: ${r.offer.hospitals.map((h: any) => shortHospital(h.name)).join(", ")}, online in ${r.offer.minutes_to_online} min, ${r.offer.effective_minutes} min of coverage`
                    : r.first_failure}
                  <span className="muted small"> {open === r.physician_id ? "Hide rules" : "Show all rules"}</span>
                </td>
              </tr>
              {open === r.physician_id && (
                <tr>
                  <td colSpan={3}>
                    {r.rules.map((x: any) => (
                      <div key={x.key} className="rule">
                        <span className={`mark ${x.passed ? "pass" : "fail"}`}>{x.passed ? "Yes" : "No"}</span>
                        <span><b>{x.label}.</b> {x.reason}</span>
                      </div>
                    ))}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function FloodGuard({ r }: any) {
  const names = r.hospital_names;
  return (
    <>
      {r.headline.map((h: string, i: number) => <p key={i}>{h}</p>)}
      {r.joiners.map((j: any) => {
        const gap = new Set(j.gap_hospitals || []);
        const max = Math.max(1, ...Object.values(j.without.by_hospital) as number[], ...Object.values(j.with.by_hospital) as number[]);
        const bars = (byH: any) =>
          Object.entries(byH).map(([h, n]: any) => (
            <div className="hbar" key={h}>
              <span className="lbl">{shortHospital(names[h])}</span>
              <span className={`b ${j.gap_hospitals && !gap.has(h) ? "out" : ""}`} style={{ width: `${(160 * n) / max}px` }} />
              <span>{n}</span>
            </div>
          ));
        return (
          <div key={j.physician_id} style={{ marginBottom: 12 }}>
            <b>{j.name}</b> <span className="muted small">({j.kind}, from {fmtTime(j.joins_at)})</span>
            <div className="fg">
              <div className="side"><h4>Without the guard: {j.without.calls} calls</h4>{bars(j.without.by_hospital)}</div>
              <div className="side"><h4>With the guard: {j.with.calls} calls</h4>{bars(j.with.by_hospital)}</div>
            </div>
          </div>
        );
      })}
      <h4>Everyone else (calls in the same window)</h4>
      <table className="t">
        <thead><tr><th>Doctor</th><th>Without</th><th>With</th><th>Change</th></tr></thead>
        <tbody>
          {r.others.map((o: any) => (
            <tr key={o.physician_id}><td>{o.name}</td><td>{o.without}</td><td>{o.with}</td><td>{o.change > 0 ? "+" : ""}{o.change}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        {r.runs} simulated runs, fixed seed {r.seed}. {r.assumptions.join(" ")} Red bars are hospitals outside the gap.
      </p>
    </>
  );
}
