import { useEffect, useState } from "react";
import { api, post } from "../api";
import { fmtTime } from "../util";
import Card from "./Card";

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
    <div className="overview">
      <Card title="Replay scoreboard" sub="When would Relay have seen it? The day replayed every few minutes using only records that had arrived.">
        <div className="card-body">
          {!score && <p className="muted">Replaying the day every 5 minutes...</p>}
          {score && (
            <>
              <ul className="headlines">{score.headlines.map((h: string) => <li key={h}>{h}</li>)}</ul>
              <details className="findings">
                <summary>All {score.items.length} findings</summary>
                <ul>{score.items.map((it: any, n: number) => <li key={n}>{it.text}</li>)}</ul>
              </details>
              <p className="muted small">{score.note} The engine reran at every {score.step_minutes} minutes using only records that had arrived by then.</p>
            </>
          )}
        </div>
      </Card>

      {ins && (
        <>
          <Card title="Handoff cards" count={ins.handoffs.length} sub="Consults held by doctors within 60 minutes of shift end">
            <div className="card-body">
              {ins.handoffs.length === 0 && <p className="muted">No doctor is within an hour of shift end.</p>}
              <div className="handoff-grid">
                {ins.handoffs.map((h: any) => {
                  const confirmed = state.handoff_confirmations?.[h.consult_id];
                  return (
                    <div key={h.consult_id} className="handoff">
                      <div className="handoff-top">
                        <b>{h.consult_id} · {h.hospital}</b>
                        <span className={`chip ${h.severity === "Stroke" ? "stroke" : "info"}`}>{h.severity}</span>
                      </div>
                      <div className="muted small">{h.physician} leaves in {h.minutes_to_shift_end} min</div>
                      <dl className="q">
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
            </div>
          </Card>

          <div className="ov-grid even">
            <Card title="Credentialing impact" sub="Who, credentialed where, would have covered today's problems">
              <div className="card-body">
                {ins.credentialing.note && <p className="callout">{ins.credentialing.note}</p>}
                <ol className="ranked">{ins.credentialing.top.slice(0, 6).map((r: any) => <li key={r.physician_id + r.hospital_id}>{r.text}</li>)}</ol>
                <table className="t boxed">
                  <thead><tr><th>Doctor on shift</th><th>Credentialed at</th><th>Consults this shift</th></tr></thead>
                  <tbody>
                    {ins.credentialing.workload.map((w: any) => (
                      <tr key={w.physician_id}><td>{w.name}</td><td>{w.credentialed_at} hospitals</td><td>{w.consults_this_shift}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card title="Time to doctor today" sub={`Median minutes from first request. Network median to first contact: ${ins.time_to_doctor.network_median_to_contact} min.`}>
              <div className="card-body">
                <table className="t boxed">
                  <thead><tr><th>Hospital</th><th>Consults</th><th>To first contact</th><th>Stroke to video</th></tr></thead>
                  <tbody>
                    {ins.time_to_doctor.hospitals.map((h: any) => (
                      <tr key={h.hospital_id}><td>{h.hospital}</td><td>{h.consults}</td><td>{h.median_to_contact ?? ""}</td><td>{h.median_stroke_to_video ?? ""}</td></tr>
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
