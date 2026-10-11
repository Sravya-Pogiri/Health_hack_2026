import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Header, { SimBar } from "./components/Header";
import Coordinator from "./components/Coordinator";
import LeadView from "./components/LeadView";
import PhoneView, { OfferScreen } from "./components/PhoneView";
import AuditView from "./components/AuditView";
import InsightsView from "./components/InsightsView";

export type Role = "coordinator" | "lead" | "phone" | "insights" | "audit";

function roleFromHash(): Role {
  const h = window.location.hash.replace("#", "");
  return (["coordinator", "lead", "phone", "insights", "audit"].includes(h) ? h : "coordinator") as Role;
}

const HEADINGS: Record<Role, [string, string]> = {
  coordinator: ["Operations overview", "Network coverage and next actions"],
  lead: ["Clinical lead", "Decisions waiting on you and stroke care at risk"],
  phone: ["Doctor phone", "What the doctor sees when an offer arrives"],
  insights: ["Insights", "What Relay would have caught today, and why"],
  audit: ["Audit log", "Every proposal, approval, message and reply"],
};

export default function App() {
  // A doctor opening an offer link on a real phone sees only that offer.
  const offerMatch = window.location.pathname.match(/^\/offer\/([^/]+)/);
  if (offerMatch) {
    return (
      <div className="phone-standalone">
        <OfferScreen offerId={offerMatch[1]} />
      </div>
    );
  }
  return <Main />;
}

function Main() {
  const [role, setRole] = useState<Role>(roleFromHash());
  const [state, setState] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await api("/api/state"));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const onHash = () => setRole(roleFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(id);
  }, [toast]);

  const changeRole = (r: Role) => {
    window.location.hash = r;
    setRole(r);
  };

  // Every action refreshes the state right away and shows a short confirmation.
  const act = async (fn: () => Promise<any>, done?: string) => {
    try {
      const result = await fn();
      if (done) setToast(done);
      await refresh();
      return result;
    } catch (e: any) {
      setToast(e.message);
    }
  };

  if (!state) {
    return <div className="empty">{error ? `Cannot reach the Relay API: ${error}` : "Loading Relay..."}</div>;
  }

  return (
    <>
      <Header state={state} role={role} setRole={changeRole} />
      <div className="page">
        <div className="page-head">
          <div>
            <h1>{HEADINGS[role][0]}</h1>
            <p>{HEADINGS[role][1]}</p>
          </div>
          <span className="demo-badge" title="All data is synthetic. Relay uses no patient information.">
            <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8"
              strokeLinecap="round" strokeLinejoin="round"><path d="M9 3h6M10 3v6l-5.5 9.5A1.7 1.7 0 006 21h12a1.7 1.7 0 001.5-2.5L14 9V3M7.5 15h9" /></svg>
            Synthetic demo
          </span>
        </div>
        {error && <div className="banner stale">Connection problem: {error}. Showing the last data received.</div>}
        {role === "coordinator" && <Coordinator state={state} act={act} />}
        {role === "lead" && <LeadView state={state} act={act} />}
        {role === "phone" && <PhoneView state={state} act={act} />}
        {role === "insights" && <InsightsView state={state} act={act} />}
        {role === "audit" && <AuditView state={state} />}
        <SimBar state={state} act={act} />
        <div className="footer">
          All data is synthetic. Relay uses no patient information: matching uses hospital, consult type, stage, and
          timers only. Role switcher is for the demo; production would use sign-in.
        </div>
      </div>
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
