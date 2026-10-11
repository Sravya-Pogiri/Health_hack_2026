import { useState } from "react";
import { post } from "../api";
import { fmtTime } from "../util";
import type { Role } from "../App";
import AddEvents from "./AddEvents";
import Icon from "./Icon";

const ROLES: [Role, string][] = [
  ["coordinator", "Coordinator"],
  ["lead", "Clinical lead"],
  ["phone", "Doctor phone"],
  ["insights", "Insights"],
  ["audit", "Audit log"],
];

const ROLE_INFO: Record<Role, { icon: string; title: string; sub: string }> = {
  coordinator: { icon: "coordinator", title: "Coordinator", sub: "Live network state, ranked problems, and who can help" },
  lead: { icon: "lead", title: "Clinical lead", sub: "Approve fixes in one step and watch stroke care" },
  phone: { icon: "phone", title: "Doctor phone", sub: "What the doctor sees when an offer arrives" },
  insights: { icon: "insights", title: "Insights", sub: "Replay scoreboard, handoffs, credentialing and time to doctor" },
  audit: { icon: "audit", title: "Audit log", sub: "Append-only record of every proposal, approval and reply" },
};

export function Sidebar({ state, role, setRole, theme, toggleTheme }: any) {
  const [health, setHealth] = useState(false);
  const dh = state.data_health;
  const pending = state.pending_approvals?.length || 0;
  const critical = state.issues.filter((g: any) => g.severity === "critical").length;
  const badge: Partial<Record<Role, { n: number; tone: string }>> = {
    coordinator: { n: critical, tone: "critical" },
    lead: { n: pending, tone: "accent" },
  };

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true">
            <path d="M9 22V10h7a4 4 0 010 8h-7m6 0l5 4" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div className="brand-text">
          Relay
          <small>for Sevaro Synapse</small>
        </div>
      </div>

      <div className="nav-label">Demo roles</div>
      <nav className="nav">
        {ROLES.map(([r, label]) => {
          const b = badge[r];
          return (
            <button key={r} className={`nav-item ${role === r ? "active" : ""}`} aria-current={role === r ? "page" : undefined} onClick={() => setRole(r)}>
              <Icon name={ROLE_INFO[r].icon} />
              <span className="nav-text">{label}</span>
              {b && b.n > 0 && <span className={`nav-badge ${b.tone}`}>{b.n}</span>}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-foot">
        <button className={`feed ${dh.stale ? "stale" : "ok"}`} onClick={() => setHealth(true)} title="Data health">
          <span className="feed-dot" />
          <span className="feed-text">
            <b>{dh.stale ? "Feed quiet" : "Feed live"}</b>
            <span>Data as of {fmtTime(dh.data_as_of)}</span>
          </span>
        </button>
        <button className="theme-toggle" onClick={toggleTheme} aria-label="Switch light or dark theme">
          <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
          <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>
        </button>
        <p className="sidebar-note">
          <Icon name="shield" size={14} />
          <span>Synthetic data. No patient information. The role switcher is for the demo.</span>
        </p>
      </div>
      {health && <HealthPanel dh={dh} onClose={() => setHealth(false)} />}
    </aside>
  );
}

export function Topbar({ state, role, act }: any) {
  const [adding, setAdding] = useState(false);
  const clock = state.clock;
  const info = ROLE_INFO[role as Role];
  const clockCmd = (body: any, msg?: string) => act(() => post("/api/clock", body), msg);
  const live = clock.mode === "data";

  return (
    <header className="topbar">
      <div className="page-title">
        <h1>{info.title}</h1>
        <p>{info.sub}</p>
      </div>
      <div className="topbar-actions">
        <div className="sim" role="group" aria-label="Demo clock">
          <span className="sim-label"><Icon name="clock" size={14} /> Sim</span>
          <button title="Advance the engine clock 1 minute" onClick={() => clockCmd({ action: "advance", minutes: 1 })}>+1m</button>
          <button title="Advance 5 minutes" onClick={() => clockCmd({ action: "advance", minutes: 5 })}>+5m</button>
          <button title="Advance 10 minutes" onClick={() => clockCmd({ action: "advance", minutes: 10 })}>+10m</button>
          {clock.mode === "playing" ? (
            <button title="Pause the clock" onClick={() => clockCmd({ action: "pause" })}><Icon name="pause" size={13} /> Pause</button>
          ) : (
            <button title="Run the clock at 10 times real speed" onClick={() => clockCmd({ action: "play", speed: 10 })}><Icon name="play" size={13} /> 10x</button>
          )}
          {!live && (
            <button title="Go back to the time of the latest data" onClick={() => clockCmd({ action: "follow_data" }, "Clock follows the data again")}>
              <Icon name="live" size={14} /> Live
            </button>
          )}
        </div>
        <div className={`now-card ${live ? "live" : "demo"}`}>
          <span className="now-time">{fmtTime(state.now)}<small> ET</small></span>
          <span className="now-mode">{live ? "Live" : clock.mode === "playing" ? "Playing" : "Demo clock"}</span>
        </div>
        <button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} /> Add events</button>
        <button
          className="btn icon-btn"
          title="Reset to the 6:20 PM kit data"
          aria-label="Reset demo"
          onClick={() => {
            if (confirm("Reset the demo to the original kit data at 6:20 PM? Uploaded events and offers are cleared.")) {
              act(() => post("/api/reset"), "Reset to the kit data");
            }
          }}
        >
          <Icon name="reset" size={16} />
        </button>
      </div>
      {adding && <AddEvents onClose={() => setAdding(false)} act={act} state={state} />}
    </header>
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
