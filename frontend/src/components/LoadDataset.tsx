import { useState } from "react";
import { api, post } from "../api";
import { fmtTime } from "../util";

// Replace the whole network (hospitals, doctors, schedules, credentials, events) with a new set of kit files.
const KIT_FILES: [string, string, boolean][] = [
  ["Hospitals", "hospitals.csv", true],
  ["Doctors on shift", "physicians_on_shift.csv", true],
  ["Credentialing", "credentialing_matrix.csv", true],
  ["Coverage requirements", "coverage_requirements.csv", true],
  ["Event stream", "event_stream.csv", true],
  ["Off-shift roster", "physicians_off_shift_roster.csv", false],
  ["Normal hourly volume", "baseline_hourly_volume.csv", false],
];

export default function LoadDataset({ state, act, onClose }: any) {
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const ds = state.dataset;

  const pick = (list: FileList | null) => {
    if (!list) return;
    const chosen = Array.from(list).filter((f) => /\.(csv|zip)$/i.test(f.name));
    setFiles((prev) => [...prev.filter((p) => !chosen.some((c) => c.name === p.name)), ...chosen]);
    setResult(null);
  };

  const load = async () => {
    if (!confirm("Replace the current data with this dataset? Offers, decisions and added events are cleared.")) return;
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    setBusy(true);
    const r = await act(() => api("/api/dataset", { form: fd }));
    setBusy(false);
    if (r) {
      setResult(r);
      if (r.ok) setFiles([]);
    }
  };

  const backToSample = async () => {
    if (!confirm("Go back to the sample data? Offers, decisions and added events are cleared.")) return;
    const r = await act(() => post("/api/dataset/sample"), "Back to the sample data");
    if (r) setResult(null);
  };

  return (
    <div className="overlay center" onClick={onClose}>
      <div className="modal ds-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ds-head">
          <div>
            <h2>Upload a new dataset</h2>
            <p className="muted small">Replaces the whole network: hospitals, doctors, schedules, credentials and events.</p>
          </div>
          <button className="btn small" onClick={onClose}>Close</button>
        </div>

        <div className="ds-current">
          <span className={`chip ${ds?.source === "uploaded" ? "accent" : "info"}`}>{ds?.source === "uploaded" ? "Uploaded" : "Sample"}</span>
          <b>{ds?.name}</b>
          <span className="muted small">{ds?.hospitals} hospitals · {ds?.physicians} doctors · {ds?.events} events · now {fmtTime(state.now)} ET</span>
          {ds?.source === "uploaded" && <button className="link ds-back" onClick={backToSample}>Back to the sample data</button>}
        </div>

        {!result?.ok && (
          <>
            <div className="ds-grid">
              <div>
                <div className="sub-title">Files Synapp needs</div>
                <ul className="ds-files">
                  {KIT_FILES.map(([label, file, required]) => (
                    <li key={file}><b>{label}</b> <span className="muted">{file}</span>{!required && <span className="chip info">optional</span>}</li>
                  ))}
                </ul>
                <p className="muted small">File names don't have to match: Synapp recognises each file by its name or its columns. Snapshot files are ignored.</p>
              </div>
              <div>
                <label className={`drop ds-drop ${over ? "over" : ""}`}
                  onDragOver={(e) => { e.preventDefault(); setOver(true); }}
                  onDragLeave={() => setOver(false)}
                  onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files); }}>
                  <b>Drop the CSV files or a zip here</b>
                  <span>or click to choose</span>
                  <input type="file" multiple accept=".csv,.zip,text/csv,application/zip" style={{ display: "none" }}
                    onChange={(e) => pick(e.target.files)} />
                </label>
                {files.length > 0 && (
                  <ul className="ds-chosen">
                    {files.map((f) => (
                      <li key={f.name}>
                        <span>{f.name}</span>
                        <button className="link muted-link" onClick={() => setFiles(files.filter((x) => x !== f))}>Remove</button>
                      </li>
                    ))}
                  </ul>
                )}
                <button className="btn primary ds-load" disabled={!files.length || busy} onClick={load}>
                  {busy ? "Uploading..." : `Upload dataset${files.length ? ` (${files.length} file${files.length === 1 ? "" : "s"})` : ""}`}
                </button>
              </div>
            </div>
          </>
        )}

        {result && <Result r={result} onClose={onClose} />}
      </div>
    </div>
  );
}

function Result({ r, onClose }: any) {
  return (
    <div className={`ds-result ${r.ok ? "ok" : "bad"}`}>
      {r.ok ? (
        <>
          <h3>Dataset uploaded</h3>
          <p>
            {r.dataset.hospitals} hospitals, {r.dataset.physicians} doctors and {r.dataset.events} events
            {r.dataset.scenario_date && <> for {r.dataset.scenario_date}</>}. Now is <b>{fmtTime(r.now)} ET</b>, the time of the latest record.
            {" "}{r.issues} item{r.issues === 1 ? "" : "s"} need attention{r.top_issue ? <>, starting with <b>{r.top_issue.group}</b>.</> : "."}
          </p>
        </>
      ) : (
        <>
          <h3>Not uploaded</h3>
          <ul>{r.errors.map((e: string) => <li key={e}>{e}</li>)}</ul>
          <p className="muted small">Nothing was changed.</p>
        </>
      )}
      {r.files?.length > 0 && (
        <table className="t boxed">
          <thead><tr><th>File</th><th>Recognised as</th><th>Rows</th><th>Status</th></tr></thead>
          <tbody>
            {r.files.map((f: any) => (
              <tr key={f.name}>
                <td>{f.name}</td><td>{f.label}</td><td>{f.rows}</td>
                <td>
                  {f.status === "ok" && <span className="chip ok">Used</span>}
                  {f.status === "ignored" && <span className="chip info">Ignored</span>}
                  {f.status === "duplicate" && <span className="chip warning">Duplicate</span>}
                  {f.status === "missing_columns" && <span className="chip critical">Missing {f.missing_columns.join(", ")}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {[...(r.warnings || []), ...(r.load_warnings || [])].length > 0 && (
        <ul className="ds-warn">{[...(r.warnings || []), ...(r.load_warnings || [])].map((w: string) => <li key={w}>{w}</li>)}</ul>
      )}
      {r.skipped_rows?.length > 0 && (
        <p className="muted small">{r.skipped_rows.length} bad row{r.skipped_rows.length === 1 ? " was" : "s were"} skipped. Details are in the data health panel (click "Updated" in the header).</p>
      )}
      {r.ok && <button className="btn primary" onClick={onClose}>See the new data</button>}
    </div>
  );
}
