import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import { capitalize, fmtTime, lastName, SEVERITY_LABEL, shortHospital } from "../util";
import { actionFor, Chevron, planStatus } from "./Coordinator";

// The shift-offer table only matters when the recommended fix is an offer.
const OFFER_KINDS = ["split_shift", "start_early", "covered_by_offer"];

const LADDER: Record<string, string> = {
  reassign: "1. Reassign on shift",
  wait: "2. Wait for assigned doctor",
  interrupt: "3. Pull off lower-priority work",
  incoming_join: "4. Doctor already joining",
  start_early: "5. Ask to start early",
  split_shift: "6. Split-shift offer",
};

// "A. B." -> ["A.", "B."]. Keeps "Dr. Kim" and "11.3 h" in one piece.
function sentences(text: string): string[] {
  const safe = (text || "").replace(/\bDr\. /g, "Dr\u0001").replace(/(\d)\.(\d)/g, "$1\u0002$2");
  const parts = safe.match(/[^.!?]+(?:[.!?]+|$)/g) || [safe];
  return parts.map((x) => x.replace(/\u0001/g, ". ").replace(/\u0002/g, ".").trim()).filter(Boolean);
}

export default function IssueDrawer({ issueId, state, act, onClose }: any) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [fg, setFg] = useState<any>(null);

  useEffect(() => {
    api(`/api/issues/${issueId}`).then(setD).catch((e) => setErr(e.message));
  }, [issueId, state.now, state.audit_count]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const names: Record<string, string> = {};
  state.physicians.forEach((p: any) => (names[p.id] = p.name));

  const plan = d?.what_to_do;
  const primary = plan?.primary;
  const showOffers = !!d?.eligibility && !!primary && OFFER_KINDS.includes(primary.kind);

  // "Harbor Regional: Stroke consult ..." -> place and problem.
  const cut = d ? d.title.indexOf(": ") : -1;
  const place = d ? (cut > 0 ? d.title.slice(0, cut) : d.where.hospital_name || d.where.physician_name || "") : "";
  const problem = d ? (cut > 0 ? d.title.slice(cut + 2) : d.title) : "";
  const where = d
    ? [d.where.hospital_name, d.where.consult_id, d.where.physician_name].filter((x) => x && x !== place).join(" · ")
    : "";

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        {err && <p>This item is no longer open: {err}</p>}
        {!d && !err && <p>Loading...</p>}
        {d && (
          <>
            <div className="dhead">
              <div className="dchips">
                <span className={`chip ${d.severity}`}>{SEVERITY_LABEL[d.severity]}</span>
                {d.stroke && <span className="chip stroke">Stroke risk</span>}
              </div>
              <button className="btn small" title="Close (Esc)" onClick={onClose}>Close</button>
            </div>
            <h2 className="dtitle">{place && <b>{place} </b>}<span>{capitalize(problem)}</span></h2>
            <p className="dwhat">{d.what}</p>
            {where && <div className="dwhere">{where}</div>}

            {plan && primary && <Recommendation d={d} state={state} act={act} />}

            <div className="dsecs">
              <Section title="Why it matters" summary={sentences(d.why)[0]}>
                <ul className="dlist">
                  {sentences(d.why).map((x, i) => <li key={i}>{x}</li>)}
                </ul>
              </Section>

              {plan?.alternatives?.length > 0 && (
                <Section title="Why not the other options" summary={`${plan.alternatives.length} ruled out`}>
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
                </Section>
              )}

              {d.strip && (
                <Section title="Coverage ahead" summary={stripSummary(d.strip)}>
                  <Strip points={d.strip} names={names} />
                </Section>
              )}

              {d.hospital?.next_up && (
                <Section title="Who gets the next call here"
                  summary={d.hospital.next_up.physician_id ? `${lastName(names[d.hospital.next_up.physician_id] || "")} would get it` : "nobody free"}>
                  <RouteTrace nextUp={d.hospital.next_up} names={names} />
                </Section>
              )}

              {showOffers && (
                <Section title="Doctors considered for an offer"
                  summary={`${d.eligibility.eligible_count} of ${d.eligibility.rows.length} eligible`}>
                  <EligibilityTable e={d.eligibility} bare />
                  <h4 style={{ margin: "18px 0 8px" }}>Split-shift trap: where would the new doctor's calls go?</h4>
                  {!fg ? (
                    <button className="btn" onClick={() => api("/api/simulate/flood-guard").then(setFg)}>
                      Run the flood guard simulation
                    </button>
                  ) : (
                    <FloodGuard r={fg} />
                  )}
                </Section>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Section({ title, summary, children }: any) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dsec">
      <button className="dsec-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="dsec-title">{title}</span>
        {!open && summary && <span className="dsec-sum">{summary}</span>}
        <span className="dsec-chev"><Chevron open={open} /></span>
      </button>
      {open && <div className="dsec-body">{children}</div>}
    </div>
  );
}

function stripSummary(points: any[]): string {
  const first = points.find((p: any) => p.short);
  return first ? `short from ${fmtTime(first.t)}` : "staffed as needed";
}

function Recommendation({ d, state, act }: any) {
  const plan = d.what_to_do;
  const p = plan.primary;
  const action = actionFor(p, d);
  const linked = d.plan_id ? state.plans.find((x: any) => x.id === d.plan_id) : null;
  return (
    <div className="dfix">
      <div className="dfix-kicker">Recommended</div>
      <div className="dfix-label">{p.label}</div>
      <div className="dfix-meta">
        {p.approver_label}
        {p.eta_minutes ? ` · help in about ${p.eta_minutes} min` : ""}
      </div>
      {p.why && <div className="dfix-why">{p.why}</div>}
      {p.warnings?.map((w: string) => <div key={w} className="warn-text">{w}</div>)}
      <div className="dfix-actions">
        {linked ? (
          <span className="chip accent">Plan {linked.id}: {planStatus(linked)}</span>
        ) : (
          <button className="btn primary" onClick={() => act(action.call, action.done)}>{action.label}</button>
        )}
      </div>
      {plan.also_now?.length > 0 && (
        <div className="dfix-more">
          {plan.also_now.map((o: any, i: number) => {
            const a = actionFor(o, d);
            return (
              <div key={i} className="qalso">
                <span><span className="muted">Also now: </span>{o.label}</span>
                <button className="btn small" onClick={() => act(a.call, a.done)}>{a.short}</button>
              </div>
            );
          })}
        </div>
      )}
      {plan.backups?.length > 0 && (
        <div className="dfix-more muted small">
          {plan.backups.map((o: any, i: number) => <div key={i}>Backup: {o.label}</div>)}
        </div>
      )}
    </div>
  );
}

function Strip({ points, names }: any) {
  const max = Math.max(3, ...points.map((p: any) => Math.max(p.staffed, p.required)));
  return (
    <>
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

export function EligibilityTable({ e, bare }: any) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      {bare ? (
        <p className="muted small">
          Off-duty doctors checked for {e.target_hospitals.map((h: any) => shortHospital(h.name)).join(", ")}. Click a row for
          every rule.
        </p>
      ) : (
        <h3>
          Every off-duty doctor considered for {e.target_hospitals.map((h: any) => shortHospital(h.name)).join(", ")} (
          {e.eligible_count} eligible)
        </h3>
      )}
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
      {r.value_of_acting && (
        <div className="summary">
          <b>Value of acting.</b> {r.value_of_acting.text}
          <div className="muted small">{r.value_of_acting.note}</div>
        </div>
      )}
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
