import { useState } from "react";
import { post } from "../api";
import { fmtTime, fmtTimeSec } from "../util";
import type { Role } from "../App";
import AddEvents from "./AddEvents";

const ROLES: [Role, string][] = [
  ["coordinator", "Coordinator"],
  ["lead", "Clinical lead"],
  ["phone", "Doctor phone"],
  ["insights", "Insights"],
  ["audit", "Audit log"],
];

export default function Header({ state, role, setRole }: any) {
  const [health, setHealth] = useState(false);
  const dh = state.data_health;
  const pending = state.pending_approvals?.length || 0;
  const demoClock = state.clock.mode !== "data";

  return (
    <header className="header">
      <div className="brand">
        <SynappMark />
        <span className="brand-name">Syn<span className="brand-accent">app</span></span>
      </div>
      <nav className="roles" aria-label="Demo role">
        {ROLES.map(([r, label]) => (
          <button key={r} className={role === r ? "active" : ""} aria-current={role === r ? "page" : undefined} onClick={() => setRole(r)}>
            {label}
            {r === "lead" && pending > 0 && <span className="tab-badge">{pending}</span>}
          </button>
        ))}
      </nav>
      <div className="asof">
        {state.clock.mode === "playing" && <span className="playing-pill">Playing {state.clock.speed}x</span>}
        <strong>{state.clock.mode === "playing" ? fmtTimeSec(state.now) : fmtTime(state.now)} ET</strong>
        <button className="asof-health" onClick={() => setHealth(true)} title="Data health">
          <span className={`dot ${dh.stale ? "stale" : "ok"}`} />
          {dh.stale ? "Feed quiet since" : "Updated"} {fmtTime(dh.data_as_of)}
          {demoClock && " (demo clock)"}
        </button>
      </div>
      {health && <HealthPanel dh={dh} onClose={() => setHealth(false)} />}
    </header>
  );
}

// The logo: an S drawn as a line joining two nodes, for connecting a doctor to a hospital.
const S_PATH = "M21.6 10.2C20.3 8.6 18.3 7.8 16 7.8c-3.2 0-5.4 1.7-5.4 4.1 0 5.6 10.8 3.4 10.8 8.6 0 2.4-2.3 4-5.5 4-2.4 0-4.4-.9-5.6-2.5";

function SynappMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="synapp-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#17a2a6" />
          <stop offset="1" stopColor="#0b5f63" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#synapp-mark)" />
      <path d={S_PATH} fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="21.6" cy="10.2" r="2.5" fill="#fff" /><circle cx="21.6" cy="10.2" r="1" fill="#0e7c80" />
      <circle cx="10.3" cy="22" r="2.5" fill="#fff" /><circle cx="10.3" cy="22" r="1" fill="#0e7c80" />
    </svg>
  );
}

// Demo controls: move the engine clock, add events, reset. Shown at the bottom of every page.
export function SimBar({ state, act }: any) {
  const [adding, setAdding] = useState(false);
  const clock = state.clock;
  const clockCmd = (body: any, msg?: string) => act(() => post("/api/clock", body), msg);

  return (
    <div className="simbar">
      <h2>Simulation controls</h2>
      <div className="sim-buttons">
        <button className="btn" title="Advance the engine clock 1 minute" onClick={() => clockCmd({ action: "advance", minutes: 1 })}>+1 min</button>
        <button className="btn" onClick={() => clockCmd({ action: "advance", minutes: 5 })}>+5 min</button>
        <button className="btn" onClick={() => clockCmd({ action: "advance", minutes: 10 })}>+10 min</button>
        {clock.mode === "playing" ? (
          <button className="btn playing" onClick={() => clockCmd({ action: "pause" }, "Clock paused")}>
            <SimIcon d="M7 5h3v14H7zM14 5h3v14h-3z" fill /> Pause ({clock.speed}x)
          </button>
        ) : (
          <>
            <button className="btn" title="60 times real speed: one minute every second" onClick={() => clockCmd({ action: "play", speed: 60 }, "Playing at 60x: one minute every second")}>
              <SimIcon d="M7 4.5l12 7.5-12 7.5z" fill /> Play 60x
            </button>
            <button className="btn" title="300 times real speed: five minutes every second" onClick={() => clockCmd({ action: "play", speed: 300 }, "Playing at 300x: five minutes every second")}>
              <SimIcon d="M4 4.5l9 7.5-9 7.5zM12 4.5l9 7.5-9 7.5z" fill /> Play 300x
            </button>
          </>
        )}
        {clock.mode !== "data" && (
          <button className="btn" title="Go back to the time of the latest data" onClick={() => clockCmd({ action: "follow_data" }, "Clock follows the data again")}>Live</button>
        )}
        <button className="btn" onClick={() => setAdding(true)}>
          <SimIcon d="M4 6a2 2 0 012-2h12a2 2 0 012 2v13a2 2 0 01-2 2H6a2 2 0 01-2-2zM4 9h16M8 2v4M16 2v4M12 12v6M9 15h6" /> Add events
        </button>
        <button
          className="btn"
          title="Back to the 6:20 PM kit data"
          onClick={() => {
            if (confirm("Reset the demo to the original kit data at 6:20 PM? Uploaded events and offers are cleared.")) {
              act(() => post("/api/reset"), "Reset to the kit data");
            }
          }}
        >
          <SimIcon d="M20 12a8 8 0 11-2.3-5.7M20 4v5h-5" /> Reset
        </button>
      </div>
      {adding && <AddEvents onClose={() => setAdding(false)} act={act} state={state} />}
    </div>
  );
}

function SimIcon({ d, fill }: { d: string; fill?: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill={fill ? "currentColor" : "none"}
      stroke={fill ? "none" : "currentColor"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
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
