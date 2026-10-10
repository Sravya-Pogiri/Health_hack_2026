import { useEffect, useState } from "react";
import { api } from "../api";
import { fmtTimeSec } from "../util";

const SKIP = new Set(["seq", "at", "actor", "action", "eligibility", "ranked"]);

export default function AuditView({ state }: any) {
  const [rows, setRows] = useState<any[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    api("/api/audit").then(setRows);
  }, [state.audit_count]);

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Audit log</h2>
        <span className="sub">append-only: every proposal with who was considered, approvals, messages sent, replies, and outcomes</span>
      </div>
      {rows.length === 0 && <div className="empty">Nothing yet. Propose an offer from the coordinator view.</div>}
      <table className="t">
        <tbody>
          {[...rows].reverse().map((r) => (
            <tr key={r.seq} onClick={() => setOpen(open === r.seq ? null : r.seq)} style={{ cursor: "pointer" }}>
              <td style={{ width: 40 }} className="muted">{r.seq}</td>
              <td style={{ width: 110 }}>{fmtTimeSec(r.at)}</td>
              <td style={{ width: 150 }}>{r.actor}</td>
              <td style={{ width: 170 }}><b>{r.action.replace(/_/g, " ")}</b></td>
              <td>
                {Object.entries(r).filter(([k]) => !SKIP.has(k)).map(([k, v]) => (
                  <div key={k} className="small"><span className="muted">{k.replace(/_/g, " ")}:</span> {typeof v === "object" ? JSON.stringify(v) : String(v)}</div>
                ))}
                {r.ranked && <div className="small"><span className="muted">ranked:</span> {r.ranked.map((c: any) => `${c.rank}. ${c.name} (${c.window})`).join("; ")}</div>}
                {r.eligibility?.rows && open === r.seq && (
                  <div className="small" style={{ marginTop: 4 }}>
                    {r.eligibility.rows.map((e: any) => (
                      <div key={e.physician_id}>{e.name}: {e.eligible ? "eligible" : `excluded (${e.first_failure})`}</div>
                    ))}
                  </div>
                )}
                {r.eligibility?.rows && open !== r.seq && <div className="small muted">Click to see who was considered and excluded.</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
