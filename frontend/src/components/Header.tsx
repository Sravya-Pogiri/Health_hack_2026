import { useState } from "react";
import { post } from "../api";
import { fmtTime } from "../util";
import type { Role } from "../App";
import AddEvents from "./AddEvents";

const ROLES: [Role, string][] = [
  ["coordinator", "Coordinator"],
  ["lead", "Clinical lead"],
  ["phone", "Doctor phone"],
  ["audit", "Audit log"],
];

export default function Header({ state, role, setRole, act }: any) {
  const [adding, setAdding] = useState(false);
  const [health, setHealth] = useState(false);
  const dh = state.data_health;
  const clock = state.clock;
  const pending = state.pending_approvals?.length || 0;

  const clockCmd = (body: any, msg?: string) => act(() => post("/api/clock", body), msg);

  return (
    <div className="header">
      <div className="brand">
        <div className="brand-mark">R</div>
        Relay <small>for Sevaro Synapse</small>
      </div>
      <div className="roles">
        <span className="demo-label">Demo role</span>
        {ROLES.map(([r, label]) => (
          <button key={r} className={role === r ? "active" : ""} onClick={() => setRole(r)}>
            {label}
            {r === "lead" && pending > 0 ? ` (${pending})` : ""}
          </button>
        ))}
      </div>
      <div className="spacer" />
      <div className="clock">
        <button className="btn small" title="Advance the engine clock 1 minute" onClick={() => clockCmd({ action: "advance", minutes: 1 })}>+1 min</button>
        <button className="btn small" onClick={() => clockCmd({ action: "advance", minutes: 5 })}>+5</button>
        <button className="btn small" onClick={() => clockCmd({ action: "advance", minutes: 10 })}>+10</button>
        {clock.mode === "playing" ? (
          <button className="btn small" onClick={() => clockCmd({ action: "pause" })}>Pause</button>
        ) : (
          <button className="btn small" title="Run the clock at 10 times real speed" onClick={() => clockCmd({ action: "play", speed: 10 })}>Play 10x</button>
        )}
        {clock.mode !== "data" && (
          <button className="btn small" title="Go back to the time of the latest data" onClick={() => clockCmd({ action: "follow_data" }, "Clock follows the data again")}>Live</button>
        )}
      </div>
      <div className="asof">
        <strong>{fmtTime(state.now)} ET</strong>
        <span>
          <button className="btn ghost small" style={{ padding: 0 }} onClick={() => setHealth(true)}>
            <span className={`dot ${dh.stale ? "stale" : "ok"}`} /> Data as of {fmtTime(dh.data_as_of)}
          </button>
          {clock.mode !== "data" && " (demo clock)"}
        </span>
      </div>
      <button className="btn primary" onClick={() => setAdding(true)}>Add events</button>
      <button
        className="btn small"
        title="Back to the 6:20 PM kit data"
        onClick={() => {
          if (confirm("Reset the demo to the original kit data at 6:20 PM? Uploaded events and offers are cleared.")) {
            act(() => post("/api/reset"), "Reset to the kit data");
          }
        }}
      >
        Reset
      </button>
      {adding && <AddEvents onClose={() => setAdding(false)} act={act} state={state} />}
      {health && <HealthPanel dh={dh} onClose={() => setHealth(false)} />}
    </div>
  );
}

function HealthPanel({ dh, onClose }: any) {
  return (
    <div className="overlay center" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="btn small close" onClick={onClose}>Close</button>
        <h2 style={{ marginTop: 0 }}>Data health</h2>
        <p>
          Last record received {dh.last_record_minutes_ago} min before now ({fmtTime(dh.data_as_of)} ET).{" "}
          {dh.stale ? <b>The feed looks quiet; screens may be out of date.</b> : "The feed is current."}
        </p>
        <p className="muted small">
          {dh.events_loaded} events loaded from {dh.files.length} file{dh.files.length === 1 ? "" : "s"}.{" "}
          {dh.duplicates_dropped} duplicate{dh.duplicates_dropped === 1 ? "" : "s"} dropped. {dh.events_pending} not yet
          applied (they happen after now).
        </p>
        <h3>Late records, placed by when they happened</h3>
        {dh.late_events.length ? (
          <ul>{dh.late_events.map((l: any) => <li key={l.event_id}>{l.message}</li>)}</ul>
        ) : (
          <p className="muted">None.</p>
        )}
        <h3>Presence agents offline</h3>
        <p>{dh.agent_offline.length ? dh.agent_offline.join(", ") : "None."}</p>
        {(dh.unknown_physicians.length > 0 || dh.unknown_hospitals.length > 0) && (
          <>
            <h3>Unknown IDs</h3>
            <p>{[...dh.unknown_physicians, ...dh.unknown_hospitals].join(", ")}</p>
          </>
        )}
        {dh.skipped_rows.length > 0 && (
          <>
            <h3>Skipped rows</h3>
            <ul>{dh.skipped_rows.map((s: any, i: number) => <li key={i}>Row {s.row}: {s.reason}</li>)}</ul>
          </>
        )}
        {dh.warnings.length > 0 && (
          <>
            <h3>Reference data warnings</h3>
            <ul>{dh.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>
          </>
        )}
      </div>
    </div>
  );
}
