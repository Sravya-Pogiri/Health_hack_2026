import { useEffect, useState } from "react";
import { api, post } from "../api";
import { fmtTime } from "../util";

export default function InsightsView({ state, act }: any) {
  const [score, setScore] = useState<any>(null);
  const [ins, setIns] = useState<any>(null);

  useEffect(() => {
    api("/api/insights").then(setIns);
  }, [state.now, state.audit_count]);
  useEffect(() => {
    api("/api/scoreboard").then(setScore);
  }, [state.now, state.data_health.events_loaded]);

  return (
    <div>
      <div className="section-title">Replay scoreboard: when would Relay have seen it?</div>
      <div className="panel" style={{ padding: "14px 18px" }}>
        {!score && <p className="muted">Replaying the day every 5 minutes...</p>}
        {score && (
          <>
            {score.headlines.map((h: string) => <p key={h} style={{ fontSize: 17, margin: "6px 0" }}>{h}</p>)}
            <details style={{ marginTop: 10 }}>
              <summary className="muted">All {score.items.length} findings</summary>
              <ul>{score.items.map((i: any, n: number) => <li key={n}>{i.text}</li>)}</ul>
            </details>
            <p className="muted small">{score.note} The engine reran at every {score.step_minutes} minutes using only records that had arrived by then.</p>
          </>
        )}
      </div>

      {ins && (
        <>
          <div className="section-title">Handoff cards: doctors within 60 minutes of shift end</div>
          {ins.handoffs.length === 0 && <div className="panel empty">No doctor is within an hour of shift end.</div>}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 10 }}>
            {ins.handoffs.map((h: any) => {
              const confirmed = state.handoff_confirmations?.[h.consult_id];
              return (
                <div key={h.consult_id} className="panel" style={{ padding: "12px 14px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                    <b>{h.consult_id} · {h.hospital}</b>
                    <span className={`chip ${h.severity === "Stroke" ? "stroke" : "info"}`}>{h.severity}</span>
                  </div>
                  <div className="muted small">{h.physician} leaves in {h.minutes_to_shift_end} min</div>
                  <dl className="q" style={{ gridTemplateColumns: "92px 1fr", fontSize: 14 }}>
                    <dt>Stage</dt><dd>{h.stage}, {h.minutes_in_stage} min</dd>
                    <dt>What's left</dt><dd>{h.whats_left}</dd>
                    <dt>Watch</dt><dd>{h.watch}</dd>
                    <dt>Plan</dt><dd>{h.suggestion}</dd>
                  </dl>
                  <div className="card-actions">
                    {confirmed ? (
                      <span className="chip ok">Confirmed at {fmtTime(confirmed)}</span>
                    ) : (
                      <button className="btn small" onClick={() => act(() => post(`/api/handoffs/${h.consult_id}/confirm`, { receiver: h.receiver_id }), "Handoff confirmed")}>
                        Receiver confirms
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="section-title">Credentialing impact: who, credentialed where, would have covered today's problems</div>
          <div className="panel" style={{ padding: "12px 16px" }}>
            {ins.credentialing.note && <p><b>{ins.credentialing.note}</b></p>}
            <ol>{ins.credentialing.top.slice(0, 6).map((r: any) => <li key={r.physician_id + r.hospital_id} style={{ margin: "4px 0" }}>{r.text}</li>)}</ol>
            <table className="t" style={{ maxWidth: 520 }}>
              <thead><tr><th>Doctor on shift</th><th>Credentialed at</th><th>Consults this shift</th></tr></thead>
              <tbody>
                {ins.credentialing.workload.map((w: any) => (
                  <tr key={w.physician_id}><td>{w.name}</td><td>{w.credentialed_at} hospitals</td><td>{w.consults_this_shift}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="section-title">Time to doctor today (minutes from first request)</div>
          <div className="panel">
            <table className="t">
              <thead><tr><th>Hospital</th><th>Consults</th><th>Median to first contact</th><th>Median stroke to video</th></tr></thead>
              <tbody>
                {ins.time_to_doctor.hospitals.map((h: any) => (
                  <tr key={h.hospital_id}><td>{h.hospital}</td><td>{h.consults}</td><td>{h.median_to_contact ?? ""}</td><td>{h.median_stroke_to_video ?? ""}</td></tr>
                ))}
              </tbody>
            </table>
            <p className="muted small" style={{ padding: "0 10px" }}>Network median to first contact: {ins.time_to_doctor.network_median_to_contact} min.</p>
          </div>
        </>
      )}
    </div>
  );
}
