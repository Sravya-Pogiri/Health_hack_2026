import { useEffect, useState } from "react";
import { api, post } from "../api";
import { fmtTime, minutesBetween } from "../util";

function durationLabel(minutes: number) {
  if (minutes < 60) return `${minutes} min`;
  const hours = minutes / 60;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} hr`;
}

function recommendation(headlines: string[]) {
  const text = headlines.find((h) => h.includes("recommended"));
  const match = text?.match(/recommended (.+) at (.+);.+online by about (.+)\.$/);
  return match ? { doctor: match[1], online: match[3] } : null;
}

function strokeLead(headlines: string[]) {
  const text = headlines.find((h) => h.startsWith("Stroke consults"));
  const match = text?.match(/median of (.+) minutes earlier \((.+) consults\)/);
  return match ? { minutes: match[1], consults: match[2] } : null;
}

function gapDetails(item: any) {
  const match = item.text.match(/coverage gap at (.+) first projected at (.+), (.+) minutes ahead/);
  return match ? { starts: match[1], lead: Number(match[3]) } : null;
}

function findingRow(item: any, now: string) {
  if (item.kind === "surge") return {
    signal: "Demand surge", location: item.hospital, detected: fmtTime(item.first_flagged),
    lead: durationLabel(minutesBetween(item.first_flagged, now)), tone: "warning",
  };
  if (item.kind === "gap") {
    const gap = gapDetails(item);
    return {
      signal: "Scheduled gap", location: item.hospital, detected: fmtTime(item.first_flagged),
      lead: gap ? durationLabel(gap.lead) : "—", tone: "projected",
    };
  }
  const match = item.text.match(/^.+ \((.+)\)/);
  return {
    signal: "Stroke risk", location: match?.[1] || item.consult_id, detected: fmtTime(item.first_flagged),
    lead: item.critical_at ? durationLabel(minutesBetween(item.first_flagged, item.critical_at)) : "At threshold",
    tone: "critical",
  };
}

export default function InsightsView({ state, act }: any) {
  const [score, setScore] = useState<any>(null);
  const [ins, setIns] = useState<any>(null);

  useEffect(() => { api("/api/insights").then(setIns); }, [state.now, state.audit_count]);
  useEffect(() => { api("/api/scoreboard").then(setScore); }, [state.now, state.data_health.events_loaded]);

  const surge = score?.items.find((item: any) => item.kind === "surge");
  const firstGap = score?.items.find((item: any) => item.kind === "gap");
  const gap = firstGap ? gapDetails(firstGap) : null;
  const help = score ? recommendation(score.headlines) : null;
  const stroke = score ? strokeLead(score.headlines) : null;
  const surgeLead = surge ? minutesBetween(surge.first_flagged, state.now) : null;

  return (
    <div className="insights-page">
      <div className="insights-intro">
        <div><h1>Operational insights</h1></div>
      </div>

      <section className="insights-section">
        <div className="insights-section-head">
          <div><h2>Time created to act</h2><p>Replay of the day using only information available at each point in time.</p></div>
        </div>
        {!score && <div className="panel empty">Replaying the day…</div>}
        {score && <div className="insights-evidence">
          <div className="insights-summary-grid">
            <div className="insights-summary-card"><span>Surge detected</span><strong>{surgeLead ?? "—"} min earlier</strong><small>{surge?.hospital || "—"} · {fmtTime(surge?.first_flagged)}</small></div>
            <div className="insights-summary-card"><span>Help available</span><strong>{help ? `~ ${help.online}` : "—"}</strong><small>{help ? `${help.doctor} · approval required` : "No recommendation"}</small></div>
            <div className="insights-summary-card"><span>Coverage warning</span><strong>{gap ? `${durationLabel(gap.lead)} ahead` : "—"}</strong><small>{firstGap ? `${firstGap.hospital} · gap at ${gap?.starts}` : "No gap projected"}</small></div>
            <div className="insights-summary-card"><span>Stroke warning</span><strong>{stroke ? `${stroke.minutes} min earlier` : "—"}</strong><small>{stroke ? `Median · ${stroke.consults} consults` : "No critical consults"}</small></div>
          </div>
          <div className="insights-findings">
            <div className="insights-subhead"><h3>Replay evidence</h3><span>{score.items.length} findings</span></div>
            <table className="t insights-table">
              <thead><tr><th>Signal</th><th>Location</th><th>First flagged</th><th>Notice</th></tr></thead>
              <tbody>{score.items.map((item: any, n: number) => {
                const row = findingRow(item, state.now);
                return <tr key={n}><td><span className={`insights-signal ${row.tone}`} />{row.signal}</td><td>{row.location}</td><td>{row.detected}</td><td><b>{row.lead}</b></td></tr>;
              })}</tbody>
            </table>
          </div>
        </div>}
      </section>

      {ins && <>
        <section className="insights-section">
          <div className="insights-section-head"><div><h2>Upcoming handoffs</h2><p>Open consults owned by doctors whose shifts end within 60 minutes.</p></div><span className="insights-count">{ins.handoffs.length}</span></div>
          {ins.handoffs.length === 0 && <div className="panel empty">No handoffs due in the next hour.</div>}
          <div className="insights-handoffs">{ins.handoffs.map((h: any) => {
            const confirmed = state.handoff_confirmations?.[h.consult_id];
            return <div key={h.consult_id} className="panel insights-handoff">
              <div className="insights-card-head"><div><b>{h.hospital}</b><span>{h.consult_id}</span></div><span className={`chip ${h.severity === "Stroke" ? "stroke" : "info"}`}>{h.severity}</span></div>
              <div className="insights-leaving"><strong>{h.minutes_to_shift_end} min</strong><span>until {h.physician} leaves</span></div>
              <div className="insights-plan"><span>Plan</span>{h.suggestion.replace(/^Best finished by /, "Finish with ").replace(/^Hand off to /, "Transfer to ")}</div>
              <details className="insights-method"><summary>Consult details</summary><p>{h.stage} · {h.minutes_in_stage} min<br />{h.watch}<br />{h.whats_left}</p></details>
              <div className="card-actions">{confirmed ? <span className="chip ok">Confirmed at {fmtTime(confirmed)}</span> : <button className="btn small" onClick={() => act(() => post(`/api/handoffs/${h.consult_id}/confirm`, { receiver: h.receiver_id }), "Handoff confirmed")}>Confirm receipt</button>}</div>
            </div>;
          })}</div>
        </section>

        <section className="insights-section">
          <div className="insights-section-head"><div><h2>Credentialing opportunities</h2><p>Where an additional hospital credential could have created another coverage option.</p></div></div>
          <div className="panel insights-compact-panel">
            <table className="t insights-table"><thead><tr><th>Doctor</th><th>Hospital</th><th>Potential signals addressed</th><th>Evidence</th></tr></thead><tbody>
              {ins.credentialing.top.slice(0, 6).map((r: any) => <tr key={r.physician_id + r.hospital_id}>
                <td><b>{r.name}</b></td><td>{r.hospital}</td><td><span className="insights-number">{r.covered}</span></td>
                <td><details className="insights-inline-details"><summary>View</summary><ul>{r.what.map((detail: string, n: number) => <li key={n}>{detail}</li>)}</ul></details></td>
              </tr>)}
              {ins.credentialing.top.length === 0 && <tr><td colSpan={4} className="muted">No opportunities identified.</td></tr>}
            </tbody></table>
            <details className="insights-method"><summary>On-shift workload and calculation notes</summary>
              <p>{ins.credentialing.note} Each count is an opportunity indicator based on a routing failure or coverage gap; it does not guarantee the event would have been prevented.</p>
              <table className="t insights-workload"><thead><tr><th>Doctor</th><th>Credentialed hospitals</th><th>Consults this shift</th></tr></thead><tbody>
                {ins.credentialing.workload.map((w: any) => <tr key={w.physician_id}><td>{w.name}</td><td>{w.credentialed_at}</td><td>{w.consults_this_shift}</td></tr>)}
              </tbody></table>
            </details>
          </div>
        </section>

        <section className="insights-section">
          <div className="insights-section-head"><div><h2>Time to doctor</h2><p>Median elapsed time from the hospital’s first request to a physician response.</p></div></div>
          <div className="panel insights-compact-panel">
            <div className="insights-time-head">
              <div><span>Network median to first contact</span><strong>{ins.time_to_doctor.network_median_to_contact} min</strong></div>
            </div>
            <table className="t insights-table"><thead><tr><th>Hospital</th><th>Completed contacts</th><th>Median to first contact</th><th>Median scans back to video (stroke)</th></tr></thead><tbody>
              {ins.time_to_doctor.hospitals.map((h: any) => <tr key={h.hospital_id}><td>{h.hospital}</td><td>{h.consults}</td><td>{h.median_to_contact != null ? `${h.median_to_contact} min` : "—"}</td><td>
                {h.median_stroke_imaging_to_video != null ? <div className="insights-stroke-metric">
                  <b>{h.median_stroke_imaging_to_video} min</b><span>{h.stroke_videos} completed cases</span>
                  {h.stroke_video_criticals > 0 && <em className="chip critical">{h.stroke_video_criticals} crossed 12-min critical limit</em>}
                  {h.stroke_video_criticals === 0 && h.stroke_video_warnings > 0 && <em className="chip warning">{h.stroke_video_warnings} crossed 6-min warning</em>}
                  {h.stroke_video_warnings === 0 && <em className="chip ok">All within 6 min</em>}
                </div> : "—"}
              </td></tr>)}
            </tbody></table>
          </div>
        </section>
      </>}
    </div>
  );
}
