import { useEffect, useState } from "react";
import { api, post } from "../api";
import { fmtTime } from "../util";

const TYPES = [
  "consult_received", "consult_triaged", "physician_assigned", "incoming_call_ringing", "incoming_call_started",
  "incoming_call_ended", "outgoing_call_started", "outgoing_call_ended", "imaging_requested", "imaging_returned",
  "video_started", "video_ended", "note_started", "note_signed", "routing_attempt_failed", "physician_status_changed",
  "camera_active", "camera_inactive", "agent_heartbeat_lost", "shift_started",
];

export default function AddEvents({ onClose, act, state }: any) {
  const [tab, setTab] = useState<"file" | "one" | "demo">("file");
  const [report, setReport] = useState<any>(null);
  const [over, setOver] = useState(false);
  const [demo, setDemo] = useState<any[]>([]);
  const [form, setForm] = useState<any>({ event_type: "consult_received", description: "Stroke consult request received" });

  useEffect(() => {
    api("/api/demo-events").then(setDemo).catch(() => setDemo([]));
  }, []);

  const send = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const r = await act(() => api("/api/events/upload", { form: fd }));
    if (r) setReport(r);
  };

  return (
    <div className="overlay center" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="btn small close" onClick={onClose}>Close</button>
        <h2 style={{ marginTop: 0 }}>Add events to the day</h2>
        <p className="muted small">
          Same 8 columns as event_stream.csv. Relay adds them to the day it already has, drops duplicates, places late
          records by when they happened, and recalculates everything. No restart.
        </p>
        {report && <Report r={report} />}
        <div className="tabs" style={{ marginTop: 12 }}>
          <button className={`btn small ${tab === "file" ? "primary" : ""}`} onClick={() => setTab("file")}>Upload a CSV</button>
          <button className={`btn small ${tab === "one" ? "primary" : ""}`} onClick={() => setTab("one")}>One event</button>
          <button className={`btn small ${tab === "demo" ? "primary" : ""}`} onClick={() => setTab("demo")}>Practice files</button>
        </div>

        {tab === "file" && (
          <label
            className={`drop ${over ? "over" : ""}`}
            style={{ display: "block", cursor: "pointer" }}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files[0]) send(e.dataTransfer.files[0]); }}
          >
            Drop a CSV here, or click to choose a file
            <input type="file" accept=".csv,text/csv" style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && send(e.target.files[0])} />
          </label>
        )}

        {tab === "one" && (
          <div>
            <label className="f">Event type</label>
            <select className="i" value={form.event_type} onChange={(e) => setForm({ ...form, event_type: e.target.value })}>
              {TYPES.map((t) => <option key={t}>{t}</option>)}
            </select>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
              <div><label className="f">Consult ID</label><input className="i" value={form.consult_id || ""} onChange={(e) => setForm({ ...form, consult_id: e.target.value })} placeholder="CON-2120" /></div>
              <div>
                <label className="f">Physician</label>
                <select className="i" value={form.physician_id || ""} onChange={(e) => setForm({ ...form, physician_id: e.target.value })}>
                  <option value="">(none)</option>
                  {state.physicians.map((p: any) => <option key={p.id} value={p.id}>{p.id} {p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="f">Hospital</label>
                <select className="i" value={form.hospital_id || ""} onChange={(e) => setForm({ ...form, hospital_id: e.target.value })}>
                  <option value="">(none)</option>
                  {state.hospitals.map((h: any) => <option key={h.id} value={h.id}>{h.id} {h.name}</option>)}
                </select>
              </div>
            </div>
            <label className="f">Description</label>
            <input className="i" value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            <label className="f">Event time (blank = now, {fmtTime(state.now)} ET)</label>
            <input className="i" value={form.event_time || ""} placeholder="2026-03-17T18:25:00-04:00" onChange={(e) => setForm({ ...form, event_time: e.target.value })} />
            <div style={{ marginTop: 12 }}>
              <button className="btn primary" onClick={async () => { const r = await act(() => post("/api/events", form)); if (r) setReport(r); }}>
                Add event
              </button>
            </div>
          </div>
        )}

        {tab === "demo" && (
          <div>
            {demo.length === 0 && <p className="muted">No practice files found.</p>}
            {demo.map((d) => (
              <div key={d.name} className="inbox-item" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <div style={{ flex: 1 }}><b>{d.name}</b><div className="muted small">{d.description}</div></div>
                <button className="btn small primary" onClick={async () => { const r = await act(() => post(`/api/demo-events/${d.name}`)); if (r) setReport(r); }}>
                  Load
                </button>
              </div>
            ))}
          </div>
        )}

      </div>
    </div>
  );
}

function Report({ r }: any) {
  if (!r.ok) return <div className="report">Could not load: {r.error}</div>;
  return (
    <div className="report">
      <b>Loaded {r.source}.</b> Added {r.added} of {r.rows_read} rows. Duplicates skipped: {r.duplicates_skipped}.{" "}
      {r.late_events.length > 0 && `Late records placed by event time: ${r.late_events.length}. `}
      {r.recalls.length > 0 && `Repeat calls about the same patient: ${r.recalls.map((x: any) => x.consult_id).join(", ")}. `}
      {r.new_consults.length > 0 && `New consults: ${r.new_consults.join(", ")}. `}
      {r.skipped.length > 0 && `Skipped rows: ${r.skipped.map((s: any) => `row ${s.row} (${s.reason})`).join("; ")}. `}
      {(r.unknown_physicians.length > 0 || r.unknown_hospitals.length > 0) && `Unknown IDs: ${[...r.unknown_physicians, ...r.unknown_hospitals].join(", ")}. `}
      Now is {fmtTime(r.now)} ET.
      {r.top_issue && (
        <div style={{ marginTop: 8 }}>
          <b>Act on first:</b> {r.top_issue.group}. {r.top_issue.what} <i>{r.top_issue.do}</i>
        </div>
      )}
      {r.new_issues.length > 0 && <div style={{ marginTop: 6 }}><b>New:</b> {r.new_issues.map((i: any) => i.what).join(" ")}</div>}
      {r.resolved_issues.length > 0 && <div style={{ marginTop: 6 }}><b>Resolved:</b> {r.resolved_issues.map((i: any) => i.title).join("; ")}</div>}
    </div>
  );
}
