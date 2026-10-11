import { Fragment, useState } from "react";
import { post } from "../api";
import { capitalize, fmtHour, fmtTime, lastName, minutesBetween, SEVERITY_LABEL, shortHospital } from "../util";
import IssueDrawer from "./IssueDrawer";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--critical)",
  warning: "var(--warning)",
  projected: "var(--projected)",
  info: "var(--info)",
};

const NO_FLAG_RANK = 1e9;
const SHIFT_END_SOON_MIN = 60;
const INTERVENTIONS_SHOWN = 3;

export default function Coordinator({ state, act }: any) {
  const [openIssue, setOpenIssue] = useState<string | null>(null);
  const dh = state.data_health;
  const pending = state.summary.pending_approvals;
  const count = (sev: string) => state.issues.filter((g: any) => g.severity === sev).length;
  const critical = count("critical");
  const comingUp = count("projected");
  const warnings = state.issues.length - critical - comingUp;

  return (
    <div className="overview">
      {dh.stale && (
        <div className="banner stale">
          No new records for {dh.last_record_minutes_ago} minutes (last at {fmtTime(dh.data_as_of)} ET). Busy doctors may
          have freed up or new consults may be waiting. Check the Synapse feed before acting.
        </div>
      )}
      <Pulse critical={critical} comingUp={comingUp} warnings={warnings} pending={pending} />

      <div className="ov-grid">
        <CoverageTable state={state} />
        <Interventions state={state} act={act} open={setOpenIssue} />
      </div>

      <DoctorList state={state} />
      {openIssue && <IssueDrawer issueId={openIssue} state={state} act={act} onClose={() => setOpenIssue(null)} />}
    </div>
  );
}

function Pulse({ critical, comingUp, warnings, pending }: any) {
  const tiles = [
    { n: critical, label: critical === 1 ? "Critical issue" : "Critical issues", tone: critical ? "critical" : "ok" },
    { n: comingUp, label: comingUp === 1 ? "Upcoming risk" : "Upcoming risks", tone: comingUp ? "warning" : "ok" },
    { n: warnings, label: warnings === 1 ? "Warning" : "Warnings", tone: warnings ? "warning" : "ok" },
    { n: pending, label: "Awaiting approval", tone: pending ? "projected" : "info" },
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

export function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"
      style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform .12s" }}>
      <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function onActivate(fn: () => void) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}


function Arrow() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3 8h10M9 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ShortMark() {
  return (
    <svg className="short-mark" width="18" height="18" viewBox="0 0 20 20" aria-label="Short">
      <circle cx="10" cy="10" r="9" fill="currentColor" />
      <path d="M10 5.5v5.5" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="10" cy="14.3" r="1.3" fill="#fff" />
    </svg>
  );
}

// ---------------------------------------------------------------------------------------------
// Hospital coverage: one row per hospital, "now" plus the next three whole hours.
// ---------------------------------------------------------------------------------------------

function CoverageTable({ state }: any) {
  const names: Record<string, string> = {};
  for (const p of state.physicians) names[p.id] = lastName(p.name);

  const times: string[] = state.projection?.times || [];
  const now = new Date(state.now).getTime();
  const cols: number[] = [];
  times.forEach((t, i) => {
    const d = new Date(t);
    if (cols.length < 3 && d.getTime() > now && d.getUTCMinutes() === 0) cols.push(i);
  });

  // Hospitals with an open issue come first, in the same order as the queue.
  const flags: Record<string, { rank: number; severity: string }> = {};
  for (const g of state.issues) {
    for (const hid of g.hospital_ids || []) {
      if (!flags[hid] || g.rank < flags[hid].rank) flags[hid] = { rank: g.rank, severity: g.severity };
    }
  }
  const hospitals = [...state.hospitals].sort(
    (a: any, b: any) => (flags[a.id]?.rank ?? NO_FLAG_RANK) - (flags[b.id]?.rank ?? NO_FLAG_RANK),
  );

  return (
    <section className="card cov-card">
      <div className="card-head">
        <h2>Hospital coverage</h2>
        <p>On shift / required <span className="sep">•</span> Free doctors shown separately <span className="sep">•</span> <span className="hint-click">Click a hospital for details</span></p>
      </div>
      <div className="cov-scroll">
        <table className="cov-table">
          <thead>
            <tr>
              <th className="h">Hospital</th>
              <th className="now">Now</th>
              {cols.map((i) => (
                <th key={i}>
                  {fmtHour(times[i])}
                  <small>+{minutesBetween(state.now, times[i])} min</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {hospitals.map((h: any) => (
              <CoverageRow key={h.id} h={h} flag={flags[h.id]} cols={cols}
                series={state.projection?.hospitals?.[h.id] || []} names={names} />
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-foot">
        <span className="legend">
          <span><i className="sw covered" />Covered</span>
          <span><i className="sw short" />Short</span>
        </span>
      </div>
    </section>
  );
}

function CoverageRow({ h, flag, cols, series, names }: any) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(!open);
  const busy = h.staffed_ids.filter((id: string) => !h.free_ids.includes(id));
  const list = (ids: string[]) => ids.map((id) => names[id] || id).join(", ");
  const shortNow = h.staffed_now < h.required_now;
  return (
    <Fragment>
      <tr className={`cov-row ${open ? "open" : ""}`} tabIndex={0} aria-expanded={open} onClick={toggle} onKeyDown={onActivate(toggle)}
        title={open ? "Click to hide details" : "Click to see who is free, busy and next up"}>
        <th scope="row">
          <span className="cv-name" title={h.name}>
            {flag && <span className="sdot" style={{ background: SEVERITY_COLOR[flag.severity] }} />}
            <span className="n">{shortHospital(h.name)}</span>
            {h.surge.active && (
              <span className="chip surge" title={`${h.surge.trailing_count} consults in the last hour; normal busy level is ${h.surge.p90}`}>
                Surge
              </span>
            )}
            <span className="cv-chev"><Chevron open={open} /></span>
          </span>
        </th>
        <td className={`now ${shortNow ? "short" : ""}`}>
          <b>{h.staffed_now} / {h.required_now}</b>
          <small className={h.free_now === 0 ? "none" : ""}>{h.free_now} free</small>
          {shortNow && <ShortMark />}
        </td>
        {cols.map((i: number) => {
          const p = series[i];
          if (!p) return <td key={i} />;
          return (
            <td key={i} className={p.short > 0 ? "short" : ""}>
              {p.staffed} / {p.required}
              {p.short > 0 && <ShortMark />}
            </td>
          );
        })}
      </tr>
      {open && (
        <tr className="cov-detail">
          <td colSpan={2 + cols.length}>
            <span className="k">Free</span>
            {h.free_ids.length ? <span className="ok-text">{list(h.free_ids)}</span> : <span className="bad-text">nobody</span>}
            {busy.length > 0 && <><span className="k gap">Busy</span>{list(busy)}</>}
            {h.unavailable_ids.length > 0 && <><span className="k gap">Out</span>{list(h.unavailable_ids)}</>}
            <span className="k gap">Next call</span>
            {h.next_up?.name ? lastName(h.next_up.name) : <span className="bad-text">nobody, the 45-second promise is at risk</span>}
            {h.surge.active && (
              <div className="cv-note">
                Surge: {h.surge.trailing_count} consults in the last hour, normal busy level is {h.surge.p90}. Normally{" "}
                {h.base_required} needed, plus 1 for the surge.
              </div>
            )}
            {h.next_change && <div className="cv-note">{h.next_change}</div>}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

// ---------------------------------------------------------------------------------------------
// Priority interventions: ranked, one open at a time.
// ---------------------------------------------------------------------------------------------

export function actionFor(opt: any, issue: any) {
  // Map a recommended option to the API call that carries it out.
  const where = issue.where || {};
  switch (opt.kind) {
    case "split_shift":
    case "start_early":
    case "covered_by_offer":
      return { label: "Propose to clinical lead", short: "Propose to clinical lead", hint: "The clinical lead approves it, then the offer goes to the doctor", call: () => post("/api/offers/propose", { issue_id: issue.id }), done: "Sent to the clinical lead for approval" };
    case "reassign":
      return { label: "Reassign", short: "Reassign", call: () => post("/api/actions/reassign", { issue_id: issue.id, consult_id: where.consult_id, physician_id: opt.physician_id }), done: "Reassignment requested" };
    case "verify":
    case "interrupt":
      return { label: "Send request", short: "Ask", call: () => post("/api/actions/contact", { issue_id: issue.id, physician_id: opt.physician_id, summary: opt.label }), done: "Request sent to the doctor" };
    case "escalate_lead":
      return { label: "Escalate", short: "Escalate", call: () => post("/api/actions/escalate", { issue_id: issue.id, summary: issue.what }), done: "Escalated to the clinical lead" };
    case "escalate_hospital":
      return { label: "Log hospital call", short: "Log call", call: () => post("/api/actions/contact", { issue_id: issue.id, target: "hospital", hospital_id: where.hospital_id, summary: opt.label }), done: "Hospital call logged" };
    case "wind_down":
    case "protect_note_time":
      return { label: "Turn on", short: "Turn on", call: () => post("/api/actions/contact", { issue_id: issue.id, physician_id: opt.physician_id, summary: opt.label }), done: "Wind-down noted" };
    case "wait":
    case "incoming_join":
      return { label: "OK, keep watching", short: "Keep watching", ack: true, call: () => post(`/api/issues/${issue.id}/ack`), done: "Acknowledged; Synapp keeps watching" };
    default:
      return { label: "Acknowledge", short: "Acknowledge", ack: true, call: () => post(`/api/issues/${issue.id}/ack`), done: "Acknowledged" };
  }
}

const isAcked = (g: any) => !!(g.issues.find((i: any) => i.id === g.primary_issue_id) || g.issues[0])?.acknowledged_at;

function Interventions({ state, act, open }: any) {
  // Acknowledged items move below the rest, so the next thing to act on is always on top and open.
  const groups = [...state.issues.filter((g: any) => !isAcked(g)), ...state.issues.filter(isAcked)];
  const [expanded, setExpanded] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const fallback = groups[0] && !isAcked(groups[0]) ? groups[0].id : null;
  const current = expanded === "" ? null : groups.some((g: any) => g.id === expanded) ? expanded : fallback;
  const shown = all ? groups : groups.slice(0, INTERVENTIONS_SHOWN);

  return (
    <section className="card iv-card">
      <div className="card-head row">
        <h2>Priority interventions</h2>
        <span className="count">{groups.length}</span>
      </div>
      {groups.length === 0 && <div className="qempty">Nothing needs attention right now.</div>}
      <div className="iv-list">
        {shown.map((g: any) => (
          <Intervention key={g.id} group={g} state={state} act={act} open={open}
            expanded={current === g.id} toggle={() => setExpanded(current === g.id ? "" : g.id)}
            onAck={() => setExpanded(null)} />
        ))}
      </div>
      {groups.length > INTERVENTIONS_SHOWN && (
        <button className="link iv-all" onClick={() => setAll(!all)}>
          {all ? "Show top priorities only" : "View all interventions"} <Arrow />
        </button>
      )}
    </section>
  );
}

function Intervention({ group, state, act, open, expanded, toggle, onAck }: any) {
  const issue = group.issues.find((i: any) => i.id === group.primary_issue_id) || group.issues[0];
  const plan = issue.what_to_do;
  const primary = plan?.primary;
  const action = primary ? actionFor(primary, issue) : null;
  const linkedPlan = issue.plan_id ? state.plans.find((p: any) => p.id === issue.plan_id) : null;
  const where = issue.where || {};
  const related = group.issues.length - 1;

  // "Harbor Regional: Stroke consult ..." -> place, then the problem.
  const cut = group.title.indexOf(": ");
  const place = shortHospital(cut > 0 ? group.title.slice(0, cut) : where.hospital_name || where.physician_name || "");
  const problem = capitalize(cut > 0 ? group.title.slice(cut + 2) : group.title);
  const sev = group.severity;

  if (!expanded) {
    return (
      <button className="iv-row" onClick={toggle} aria-expanded={false}>
        <span className={`iv-dot ${sev}`} />
        <span className="iv-row-text">
          <span className="iv-place">{place}</span>
          <b>{problem}</b>
        </span>
        {issue.acknowledged_at && <span className="chip ok">Acknowledged</span>}
        <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true" className="iv-go">
          <path d="M6 3.5l4.5 4.5L6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    );
  }

  return (
    <div className={`iv-open ${sev}`}>
      <div className="iv-top">
        <span className={`iv-dot ${sev}`} />
        <span className={`chip ${sev}`}>{SEVERITY_LABEL[sev] || "Info"}</span>
        {group.stroke && <span className="chip stroke">Stroke</span>}
        {place && <><span className="bar" /> <span className="iv-where">{place}</span></>}
        {issue.acknowledged_at && <span className="chip ok">Acknowledged</span>}
        <button className="iv-collapse" aria-label="Collapse" onClick={toggle}><Chevron open /></button>
      </div>
      <h3>{problem}</h3>
      <p className="iv-what">{issue.what}</p>
      {primary && (
        <div className="iv-rec">
          <div className="iv-rec-k">
            <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8"
              strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0012 3z" /></svg>
            Recommended action
          </div>
          <div className="iv-rec-v">{primary.label}</div>
          {primary.warnings?.length > 0 && <div className="warn-text">{primary.warnings.join(" ")}</div>}
        </div>
      )}
      <div className="iv-actions">
        {linkedPlan ? (
          <span className="chip accent big" title={`Plan ${linkedPlan.id}`}>{capitalize(planStatus(linkedPlan))}</span>
        ) : (
          action && (
            <button className="btn primary big" title={(action as any).hint}
              onClick={async () => { await act(action.call, action.done); if ((action as any).ack) onAck(); }}>
              {action.label}
            </button>
          )
        )}
        <button className="link underline" onClick={() => open(issue.id)}>View reasoning</button>
        {!issue.acknowledged_at && !(action as any)?.ack && (
          <button className="link muted-link" onClick={async () => { await act(() => post(`/api/issues/${issue.id}/ack`), "Acknowledged"); onAck(); }}>Acknowledge</button>
        )}
      </div>
      {plan?.also_now?.length > 0 && (
        <div className="iv-also">
          {plan.also_now.map((o: any, i: number) => {
            const a = actionFor(o, issue);
            return (
              <div key={i} className="qalso">
                <span><span className="muted">Also now:</span> {o.label}</span>
                <button className="btn small" onClick={() => act(a.call, a.done)}>{a.short}</button>
              </div>
            );
          })}
        </div>
      )}
      {related > 0 && <div className="iv-related muted small">{related} related item{related === 1 ? "" : "s"} in the reasoning.</div>}
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

// ---------------------------------------------------------------------------------------------
// Doctors on shift: a compact strip, with the full roster (and the reasons) on demand.
// ---------------------------------------------------------------------------------------------

type Tone = "free" | "busy" | "check" | "out";

const TONE_COLOR: Record<Tone, string> = {
  free: "var(--ok)",
  busy: "var(--faint)",
  check: "var(--warning)",
  out: "var(--critical)",
};

function doctorTone(p: any): Tone {
  const code = p.verdict.code;
  if (code === "unavailable") return "out";
  if (code === "probably_busy" || code === "unverified" || (p.flags || []).includes("agent_offline")) return "check";
  if (code === "available" || code === "in_meeting") return "free";
  return "busy";
}

const TONE_ORDER: Record<Tone, number> = { free: 0, check: 1, busy: 2, out: 3 };

const PILL_LABEL: Record<Tone, string> = { free: "Available", busy: "Busy", check: "Check", out: "Unavailable" };

function DoctorList({ state }: any) {
  const [roster, setRoster] = useState(false);
  const [showOff, setShowOff] = useState(false);
  const docs = state.physicians.filter((p: any) => p.on_shift || state.joins.some((j: any) => j.physician_id === p.id));
  const off = state.physicians.filter((p: any) => !docs.includes(p));
  // Free doctors first, so the people you can use right now are at the top.
  const sorted = [...docs].sort((a: any, b: any) => TONE_ORDER[doctorTone(a)] - TONE_ORDER[doctorTone(b)]);

  return (
    <section className="card dr-card">
      <div className="card-head row">
        <h2>Doctors on shift</h2>
        <span className="count">{docs.length}</span>
        <button className="link push" onClick={() => setRoster(!roster)} aria-expanded={roster}>
          {roster ? "Hide roster" : "View roster"} <Arrow />
        </button>
      </div>
      {!roster && (
        <div className="dr-strip">
          {sorted.map((p: any) => {
            const tone = doctorTone(p);
            return (
              <div key={p.id} className="dr-chip" title={p.verdict.reason}>
                <span className="dr-name">{p.name}</span>
                <span className={`pill ${tone}`}>{tone === "check" ? p.verdict.label.replace(/\s*\(.*\)$/, "") : PILL_LABEL[tone]}</span>
              </div>
            );
          })}
        </div>
      )}
      {roster && (
        <>
          <table className="dr">
            <thead>
              <tr><th>Doctor</th><th>Status</th><th>Doing</th><th>Ends</th><th>Consults</th></tr>
            </thead>
            <tbody>
              {sorted.map((p: any) => <DoctorRow key={p.id} p={p} />)}
            </tbody>
          </table>
          <div className="dr-foot muted small">
            Click a doctor for the reason.
            {off.length > 0 && (
              <>
                {" "}
                <button className="fold inline" aria-expanded={showOff} onClick={() => setShowOff(!showOff)}>
                  <Chevron open={showOff} /> Off shift ({off.length})
                </button>
                {showOff && <span> {off.map((p: any) => lastName(p.name)).join(", ")}</span>}
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function DoctorRow({ p }: any) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(!open);
  const endsSoon = p.minutes_to_shift_end !== null && p.minutes_to_shift_end <= SHIFT_END_SOON_MIN;
  const bits = [
    p.activity_text,
    p.shift?.label && `Shift ${p.shift.label}`,
    p.long_case && "Long case",
    p.load && `${p.consults_this_shift} consults this shift (${p.load.level})`,
    p.open_consults.length > 0 && `${p.open_consults.length} open`,
    p.self_reported_status !== "Available" && `Says: ${p.self_reported_status}`,
    p.desktop_agent === "Offline" && "Desktop agent offline",
  ].filter(Boolean);
  return (
    <Fragment>
      <tr className="dr-row" tabIndex={0} aria-expanded={open} onClick={toggle} onKeyDown={onActivate(toggle)}>
        <td>
          <span className="cv-name" title={p.name}>
            <span className="sdot" style={{ background: TONE_COLOR[doctorTone(p)] }} />
            <span className="n">{p.name}</span>
            <span className="cv-chev"><Chevron open={open} /></span>
          </span>
        </td>
        <td>{p.verdict.label}</td>
        <td title={p.activity_text}>
          {p.activity_text}
          {p.free_in_minutes > 0 && <span className="muted"> · free in about {p.free_in_minutes} min</span>}
        </td>
        <td className={endsSoon ? "soon" : ""} title={endsSoon ? `Shift ends in ${p.minutes_to_shift_end} min` : undefined}>
          {fmtHour(p.shift?.end)}
        </td>
        <td>{p.consults_this_shift}</td>
      </tr>
      {open && (
        <tr className="dr-detail">
          <td colSpan={5}>
            {p.verdict.reason}
            {bits.length > 0 && <div className="cv-note">{bits.join(" · ")}</div>}
          </td>
        </tr>
      )}
    </Fragment>
  );
}
