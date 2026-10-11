import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Header from "./components/Header";
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
      <Header state={state} role={role} setRole={changeRole} act={act} />
      <div className="page">
        {error && <div className="banner stale">Connection problem: {error}. Showing the last data received.</div>}
        {role === "coordinator" && <Coordinator state={state} act={act} />}
        {role === "lead" && <LeadView state={state} act={act} />}
        {role === "phone" && <PhoneView state={state} act={act} />}
        {role === "insights" && <InsightsView state={state} act={act} />}
        {role === "audit" && <AuditView state={state} />}
        <div className="footer">
          All data is synthetic. Relay uses no patient information: matching uses hospital, consult type, stage, and
          timers only. Role switcher is for the demo; production would use sign-in.
        </div>
      </div>
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
