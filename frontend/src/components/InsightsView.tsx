import { useEffect, useState } from "react";
import { api, post } from "../api";
import { fmtTime, minutesBetween } from "../util";
import Card from "./Card";

// Layout and wording of the insights from claude/brave-euler-zcdpmg, shown in the shared card style.

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
    <div className="overview">
      <Card title="Time created to act" sub="Replay of the day using only information available at each point in time.">
        <div className="card-body">
          {!score && <p className="muted">Replaying the day...</p>}
          {score && (
            <>
              <div className="pulse insights-pulse">
                <div className="pulse-tile warning"><span className="n">{surgeLead ?? "—"} min</span><span className="l">Surge detected earlier<small>{surge?.hospital || "—"} · {fmtTime(surge?.first_flagged)}</small></span></div>
                <div className="pulse-tile ok"><span className="n">{help ? `~${help.online}` : "—"}</span><span className="l">Help available<small>{help ? `${help.doctor} · approval required` : "No recommendation"}</small></span></div>
                <div className="pulse-tile projected"><span className="n">{gap ? durationLabel(gap.lead) : "—"}</span><span className="l">Coverage warning ahead<small>{firstGap ? `${firstGap.hospital} · gap at ${gap?.starts}` : "No gap projected"}</small></span></div>
                <div className="pulse-tile critical"><span className="n">{stroke ? `${stroke.minutes} min` : "—"}</span><span className="l">Stroke warning earlier<small>{stroke ? `Median · ${stroke.consults} consults` : "No critical consults"}</small></span></div>
              </div>
              <div className="sub-title">Replay evidence · {score.items.length} findings</div>
              <table className="t boxed">
                <thead><tr><th>Signal</th><th>Location</th><th>First flagged</th><th>Notice</th></tr></thead>
                <tbody>
                  {score.items.map((item: any, n: number) => {
                    const row = findingRow(item, state.now);
                    return (
                      <tr key={n}>
                        <td><span className={`iv-dot ${row.tone} inline`} />{row.signal}</td>
                        <td>{row.location}</td><td>{row.detected}</td><td><b>{row.lead}</b></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </div>
      </Card>

      {ins && (
        <>
          <Card title="Upcoming handoffs" count={ins.handoffs.length} sub="Open consults owned by doctors whose shifts end within 60 minutes.">
            <div className="card-body">
              {ins.handoffs.length === 0 && <p className="muted">No handoffs due in the next hour.</p>}
              <div className="handoff-grid">
                {ins.handoffs.map((h: any) => {
                  const confirmed = state.handoff_confirmations?.[h.consult_id];
                  return (
                    <div key={h.consult_id} className="handoff">
                      <div className="handoff-top">
                        <div><b>{h.hospital}</b><div className="muted small">{h.consult_id}</div></div>
                        <span className={`chip ${h.severity === "Stroke" ? "stroke" : "info"}`}>{h.severity}</span>
                      </div>
                      <div className="handoff-leaving"><strong>{h.minutes_to_shift_end} min</strong><span>until {h.physician} leaves</span></div>
                      <div className="handoff-plan"><span>Plan</span>{h.suggestion.replace(/^Best finished by /, "Finish with ").replace(/^Hand off to /, "Transfer to ")}</div>
                      <details className="findings"><summary>Consult details</summary><p className="small">{h.stage} · {h.minutes_in_stage} min<br />{h.watch}<br />{h.whats_left}</p></details>
                      <div className="card-actions">
                        {confirmed ? (
                          <span className="chip ok">Confirmed at {fmtTime(confirmed)}</span>
                        ) : (
                          <button className="btn small" onClick={() => act(() => post(`/api/handoffs/${h.consult_id}/confirm`, { receiver: h.receiver_id }), "Handoff confirmed")}>
                            Confirm receipt
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </Card>

          <div className="ov-grid even">
            <Card title="Credentialing opportunities" sub="Where an additional hospital credential could have created another coverage option.">
              <div className="card-body">
                <table className="t boxed">
                  <thead><tr><th>Doctor</th><th>Hospital</th><th>Signals addressed</th><th>Evidence</th></tr></thead>
                  <tbody>
                    {ins.credentialing.top.slice(0, 6).map((r: any) => (
                      <tr key={r.physician_id + r.hospital_id}>
                        <td><b>{r.name}</b></td><td>{r.hospital}</td><td><span className="num-badge">{r.covered}</span></td>
                        <td><details className="findings"><summary>View</summary><ul>{r.what.map((d: string, n: number) => <li key={n}>{d}</li>)}</ul></details></td>
                      </tr>
                    ))}
                    {ins.credentialing.top.length === 0 && <tr><td colSpan={4} className="muted">No opportunities identified.</td></tr>}
                  </tbody>
                </table>
                <details className="findings">
                  <summary>On-shift workload and calculation notes</summary>
                  <p className="small">{ins.credentialing.note} Each count is an opportunity indicator based on a routing failure or coverage gap; it does not guarantee the event would have been prevented.</p>
                  <table className="t boxed">
                    <thead><tr><th>Doctor</th><th>Credentialed hospitals</th><th>Consults this shift</th></tr></thead>
                    <tbody>
                      {ins.credentialing.workload.map((w: any) => <tr key={w.physician_id}><td>{w.name}</td><td>{w.credentialed_at}</td><td>{w.consults_this_shift}</td></tr>)}
                    </tbody>
                  </table>
                </details>
              </div>
            </Card>

            <Card title="Time to doctor" sub="Median elapsed time from the hospital's first request to a physician response."
              right={<div className="stat-chip"><span>Network median to first contact</span><b>{ins.time_to_doctor.network_median_to_contact} min</b></div>}>
              <div className="card-body">
                <table className="t boxed">
                  <thead><tr><th>Hospital</th><th>Contacts</th><th>To first contact</th><th>Scans back to video (stroke)</th></tr></thead>
                  <tbody>
                    {ins.time_to_doctor.hospitals.map((h: any) => (
                      <tr key={h.hospital_id}>
                        <td>{h.hospital}</td><td>{h.consults}</td>
                        <td>{h.median_to_contact != null ? `${h.median_to_contact} min` : "—"}</td>
                        <td>
                          {h.median_stroke_imaging_to_video != null ? (
                            <div className="stroke-metric">
                              <b>{h.median_stroke_imaging_to_video} min</b>
                              <span className="muted">{h.stroke_videos} case{h.stroke_videos === 1 ? "" : "s"}</span>
                              {h.stroke_video_criticals > 0 && <span className="chip critical">{h.stroke_video_criticals} over 12-min limit</span>}
                              {h.stroke_video_criticals === 0 && h.stroke_video_warnings > 0 && <span className="chip warning">{h.stroke_video_warnings} over 6-min warning</span>}
                              {h.stroke_video_warnings === 0 && <span className="chip ok">All within 6 min</span>}
                            </div>
                          ) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
