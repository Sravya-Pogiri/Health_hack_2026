import { Fragment, useState } from "react";
import { post } from "../api";
import { capitalize, fmtHour, fmtTime, lastName, shortHospital } from "../util";
import IssueDrawer from "./IssueDrawer";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--critical)",
  warning: "var(--warning)",
  projected: "var(--projected)",
  info: "var(--info)",
};

const NO_FLAG_RANK = 1e9;
const SHIFT_END_SOON_MIN = 60;

export default function Coordinator({ state, act }: any) {
  const [openIssue, setOpenIssue] = useState<string | null>(null);
  const dh = state.data_health;
  const pending = state.summary.pending_approvals;
  const critical = state.issues.filter((g: any) => g.severity === "critical");
  const comingUp = state.issues.filter((g: any) => g.severity === "projected");
  const warnings = state.issues.filter((g: any) => g.severity !== "critical" && g.severity !== "projected");
  const rowProps = { state, act, open: setOpenIssue };

  return (
    <div className="lead">
      {dh.stale && (
        <div className="banner stale">
          No new records for {dh.last_record_minutes_ago} minutes (last at {fmtTime(dh.data_as_of)} ET). Busy doctors may
          have freed up or new consults may be waiting. Check the Synapse feed before acting.
        </div>
      )}
      <Pulse critical={critical.length} comingUp={comingUp.length} warnings={warnings.length} pending={pending} />

      <div className="section-title">Coverage</div>
      <CoverageTable state={state} />

      <div className="section-title">
        Do next ({critical.length}) <span className="hint">click a row for the reasoning</span>
      </div>
      <div className="panel qlist">
        {critical.map((g: any) => <QueueRow key={g.id} group={g} {...rowProps} />)}
        {critical.length === 0 && <div className="qempty">Nothing critical right now.</div>}
        <Fold label="Coming up" groups={comingUp} defaultOpen={critical.length === 0} {...rowProps} />
        <Fold label="Warnings" groups={warnings} defaultOpen={critical.length === 0 && comingUp.length === 0} {...rowProps} />
      </div>

      <DoctorList state={state} />
      {openIssue && <IssueDrawer issueId={openIssue} state={state} act={act} onClose={() => setOpenIssue(null)} />}
    </div>
  );
}

// Four tiles in the same style as the Clinical lead page.
function Pulse({ critical, comingUp, warnings, pending }: any) {
  const tiles = [
    { n: critical, label: `critical item${critical === 1 ? "" : "s"} to act on`, tone: critical ? "critical" : "ok" },
    { n: comingUp, label: `item${comingUp === 1 ? "" : "s"} coming up`, tone: comingUp ? "projected" : "ok" },
    { n: warnings, label: `warning${warnings === 1 ? "" : "s"} to watch`, tone: warnings ? "warning" : "ok" },
    { n: pending, label: "waiting on the clinical lead", tone: pending ? "projected" : "info" },
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

// ---------------------------------------------------------------------------------------------
// Coverage: one row per hospital, "now" plus the next three whole hours.
// ---------------------------------------------------------------------------------------------

function CoverageTable({ state }: any) {
  const names: Record<string, string> = {};
  for (const p of state.physicians) names[p.id] = lastName(p.name);

  const now = new Date(state.now).getTime();
  const times: string[] = state.projection?.times || [];
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
    <div className="panel cov">
      <div className="cov-scroll">
        <table className="cov-grid cov-hosp">
          <thead>
            <tr>
              <th />
              <th>Now</th>
              {cols.map((i) => <th key={i}>{fmtHour(times[i])}</th>)}
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
      <div className="cov-legend muted small">
        <span><i className="sw short" />Short</span>
        <span><i className="sw ok" />Covered</span>
        <span>Cells show credentialed doctors on shift / needed. Click a hospital to see who is free.</span>
      </div>
    </div>
  );
}

function CoverageRow({ h, flag, cols, series, names }: any) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(!open);
  const busy = h.staffed_ids.filter((id: string) => !h.free_ids.includes(id));
  const list = (ids: string[]) => ids.map((id) => names[id] || id).join(", ");
  return (
    <Fragment>
      <tr className="cov-row" tabIndex={0} aria-expanded={open} onClick={toggle} onKeyDown={onActivate(toggle)}>
        <th scope="row">
          <span className="cv-name" title={h.name}>
            <span className="sdot" style={{ background: flag ? SEVERITY_COLOR[flag.severity] : "transparent" }} />
            <span className="n">{shortHospital(h.name)}</span>
            {h.surge.active && (
              <span className="chip warning tiny" title={`${h.surge.trailing_count} consults in the last hour; normal busy level is ${h.surge.p90}`}>
                Surge
              </span>
            )}
            <span className="cv-chev"><Chevron open={open} /></span>
          </span>
        </th>
        <td className={`now ${h.staffed_now < h.required_now ? "short" : "ok"}`}>
          {h.staffed_now}/{h.required_now}
          <span className={`cv-free ${h.free_now === 0 ? "none" : ""}`}>{h.free_now} free</span>
        </td>
        {cols.map((i: number) => {
          const p = series[i];
          return p ? (
            <td key={i} className={p.short > 0 ? "short" : "ok"}>{p.staffed}/{p.required}</td>
          ) : (
            <td key={i} />
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
// Action queue
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
      return { label: "OK, keep watching", short: "Keep watching", ack: true, call: () => post(`/api/issues/${issue.id}/ack`), done: "Acknowledged; Relay keeps watching" };
    default:
      return { label: "Acknowledge", short: "Acknowledge", ack: true, call: () => post(`/api/issues/${issue.id}/ack`), done: "Acknowledged" };
  }
}

function Fold({ label, groups, defaultOpen, ...rowProps }: any) {
  const [open, setOpen] = useState(!!defaultOpen);
  if (groups.length === 0) return null;
  const toggle = () => setOpen(!open);
  return (
    <>
      <button className="fold" aria-expanded={open} onClick={toggle}>
        <Chevron open={open} /> {label} ({groups.length})
      </button>
      {open && groups.map((g: any) => <QueueRow key={g.id} group={g} {...rowProps} />)}
    </>
  );
}

function QueueRow({ group, state, act, open }: any) {
  const [expanded, setExpanded] = useState(false);
  const toggle = () => setExpanded(!expanded);
  const issue = group.issues.find((i: any) => i.id === group.primary_issue_id) || group.issues[0];
  const plan = issue.what_to_do;
  const primary = plan?.primary;
  const action = primary ? actionFor(primary, issue) : null;
  const linkedPlan = issue.plan_id ? state.plans.find((p: any) => p.id === issue.plan_id) : null;
  const others = group.issues.filter((i: any) => i.id !== issue.id);
  const where = issue.where || {};
  const whereText = [where.hospital_name, where.consult_id, where.physician_name].filter(Boolean).join(" · ");

  // "Harbor Regional: Stroke consult ..." -> bold place, then the problem.
  const cut = group.title.indexOf(": ");
  const place = cut > 0 ? group.title.slice(0, cut) : "";
  const problem = cut > 0 ? group.title.slice(cut + 2) : group.title;

  return (
    <div className="qitem">
      <div className="qrow" style={{ borderLeftColor: SEVERITY_COLOR[group.severity] || SEVERITY_COLOR.info }}>
        <span className="risk-where" title={place}>{shortHospital(place || where.hospital_name || where.physician_name || "")}</span>
        <div className="qmain" role="button" tabIndex={0} aria-expanded={expanded} onClick={toggle} onKeyDown={onActivate(toggle)}>
          <div className="qtitle">
            <span>{capitalize(problem)}</span>
            {issue.acknowledged_at && <span className="chip ok" style={{ marginLeft: 8 }}>Acknowledged</span>}
          </div>
          {primary && <div className="qaction" title={primary.label}>{primary.label}</div>}
        </div>
        {linkedPlan ? (
          <span className="chip accent qplan" title={`Plan ${linkedPlan.id}: ${planStatus(linkedPlan)}`}>
            {planStatus(linkedPlan)}
          </span>
        ) : (
          action && (
            <button className="btn small" title={(action as any).hint} onClick={() => act(action.call, action.done)}>{action.short}</button>
          )
        )}
        <button className="qchev" aria-label={expanded ? "Hide reasoning" : "Show reasoning"} onClick={toggle}>
          <Chevron open={expanded} />
        </button>
      </div>
      {expanded && (
        <div className="qdetail">
          <dl className="qdl">
            <dt>What</dt>
            <dd>{issue.what}</dd>
            <dt>Why</dt>
            <dd>{issue.why}</dd>
            {primary && (
              <>
                <dt>Fix</dt>
                <dd>
                  {primary.label}
                  {primary.why && <div className="muted small">{primary.why}</div>}
                  {primary.warnings?.length > 0 && <div className="warn-text">{primary.warnings.join(" ")}</div>}
                </dd>
                <dt>Approver</dt>
                <dd>
                  {primary.approver_label}
                  {primary.eta_minutes ? ` · help in about ${primary.eta_minutes} min` : ""}
                </dd>
              </>
            )}
            {whereText && (
              <>
                <dt>Where</dt>
                <dd>{whereText}</dd>
              </>
            )}
            {linkedPlan && (
              <>
                <dt>Plan</dt>
                <dd>{linkedPlan.id}: {planStatus(linkedPlan)}</dd>
              </>
            )}
            {plan?.also_now?.length > 0 && (
              <>
                <dt>Also now</dt>
                <dd>
                  {plan.also_now.map((o: any, i: number) => {
                    const a = actionFor(o, issue);
                    return (
                      <div key={i} className="qalso">
                        <span>{o.label}</span>
                        <button className="btn small" onClick={() => act(a.call, a.done)}>{a.short}</button>
                      </div>
                    );
                  })}
                </dd>
              </>
            )}
            {others.length > 0 && (
              <>
                <dt>Related</dt>
                <dd>
                  {others.map((o: any) => (
                    <div key={o.id} className="qalso">
                      <span>
                        <span className="sdot" style={{ background: SEVERITY_COLOR[o.severity] || SEVERITY_COLOR.info, marginRight: 8 }} />
                        {o.what}
                      </span>
                      <button className="btn ghost small" onClick={() => open(o.id)}>Details</button>
                    </div>
                  ))}
                </dd>
              </>
            )}
          </dl>
          <div className="qdetail-actions">
            <button className="btn small" onClick={() => open(issue.id)}>Full details and why not</button>
            {!issue.acknowledged_at && !(action as any)?.ack && (
              <button className="btn ghost small" onClick={() => act(() => post(`/api/issues/${issue.id}/ack`), "Acknowledged")}>
                Acknowledge
              </button>
            )}
          </div>
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

// ---------------------------------------------------------------------------------------------
// Doctors on shift: one row each, reasoning on click.
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

function DoctorList({ state }: any) {
  const [showOff, setShowOff] = useState(false);
  const docs = state.physicians.filter((p: any) => p.on_shift || state.joins.some((j: any) => j.physician_id === p.id));
  const off = state.physicians.filter((p: any) => !docs.includes(p));
  // Free doctors first, so the people you can use right now are at the top.
  const sorted = [...docs].sort((a: any, b: any) => TONE_ORDER[doctorTone(a)] - TONE_ORDER[doctorTone(b)]);

  return (
    <>
      <div className="section-title">Doctors on shift</div>
      <div className="panel">
        <table className="dr">
          <thead>
            <tr><th>Doctor</th><th>Status</th><th>Doing</th><th>Ends</th><th>Consults</th></tr>
          </thead>
          <tbody>
            {sorted.map((p: any) => <DoctorRow key={p.id} p={p} />)}
          </tbody>
        </table>
      </div>
      <div className="cov-legend muted small">
        {(["free", "busy", "check", "out"] as Tone[]).map((t) => (
          <span key={t}><span className="sdot" style={{ background: TONE_COLOR[t], marginRight: 5 }} />{t}</span>
        ))}
        <span>Click a doctor for the reason.</span>
      </div>
      {off.length > 0 && (
        <div className="offshift">
          <button className="fold inline" aria-expanded={showOff} onClick={() => setShowOff(!showOff)}>
            <Chevron open={showOff} /> Off shift ({off.length})
          </button>
          {showOff && <span className="muted small"> {off.map((p: any) => lastName(p.name)).join(", ")}</span>}
        </div>
      )}
    </>
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
