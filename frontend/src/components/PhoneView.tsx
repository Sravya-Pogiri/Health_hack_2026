import { useCallback, useEffect, useState } from "react";
import { api, post } from "../api";
import { download, fmtTime, icsFor, mmss } from "../util";
import Card from "./Card";

// Doctor phone (demo): pick a doctor, see their offers on a phone-sized screen.
export default function PhoneView({ state }: any) {
  const byDoc: Record<string, any[]> = {};
  for (const o of state.offers) (byDoc[o.physician_id] ||= []).push(o);
  const docs = Object.keys(byDoc);
  const [doc, setDoc] = useState<string | null>(null);
  const current = doc && byDoc[doc] ? doc : docs[0];
  const offers = current ? byDoc[current] : [];
  const [offerId, setOfferId] = useState<string | null>(null);
  const shown = offers.find((o: any) => o.id === offerId) || offers[offers.length - 1];

  return (
    <div className="phone-layout">
      <Card title="Doctors with offers" count={docs.length} sub="Pick a doctor to see what their phone shows">
        <div className="card-body">
          {docs.length === 0 && <p className="muted small">No offers yet. Approve one in the Clinical lead tab and it appears here.</p>}
          <div className="iv-list flush">
            {docs.map((d) => (
              <button key={d} className={`iv-row ${d === current ? "selected" : ""}`} onClick={() => { setDoc(d); setOfferId(null); }}>
                <span className={`iv-dot ${byDoc[d].some((o: any) => o.status === "sent") ? "warning" : "ok"}`} />
                <span className="iv-row-text">
                  <b>{byDoc[d][0].physician_name}</b>
                  <span className="iv-place">{byDoc[d].map((o: any) => `${o.id}: ${o.status}`).join(", ")}</span>
                </span>
              </button>
            ))}
          </div>
          {offers.length > 1 && (
            <>
              <div className="sub-title">Offers for this doctor</div>
              <div className="iv-list flush">
                {offers.map((o: any) => (
                  <button key={o.id} className={`iv-row ${shown && o.id === shown.id ? "selected" : ""}`} onClick={() => setOfferId(o.id)}>
                    <span className="iv-row-text"><b>{o.window_label}</b><span className="iv-place">{o.id}, {o.status}</span></span>
                  </button>
                ))}
              </div>
            </>
          )}
          {shown && (
            <p className="small muted phone-note">
              Real-phone link: <a href={`/offer/${shown.id}`} target="_blank" rel="noreferrer">/offer/{shown.id}</a>.
              Delivered by {shown.channel_label}
              {shown.delivery?.status && shown.delivery.status !== "simulated" ? ` (${shown.delivery.status}).` : " (simulated)."}
              {shown.channel === "sms" && " A text reply of 1 accepts and 2 declines."}
            </p>
          )}
        </div>
      </Card>
      <Card title="Phone preview" sub="Exactly what the doctor receives: hospitals, hours and why them. No patient details.">
        <div className="phone-stage">
          <div className="phone">
            <div className="phone-screen">
              {shown ? <OfferScreen offerId={shown.id} /> : (
                <div className="phone-empty">
                  <b>No offers yet</b>
                  <span>When the clinical lead approves an offer, it arrives here with a 10-minute countdown.</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

export function OfferScreen({ offerId }: { offerId: string }) {
  const [o, setO] = useState<any>(null);
  const [reply, setReply] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => api(`/api/offers/${offerId}`).then(setO).catch((e) => setErr(e.message)), [offerId]);
  useEffect(() => {
    setReply(null);
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [load]);

  if (err) return <div className="phone-msg">This offer could not be found.</div>;
  if (!o) return <div className="phone-msg muted">Loading...</div>;

  const respond = async (what: "accept" | "decline") => {
    // Re-check the offer right before replying, so a filled shift is never shown as open.
    const fresh = await api(`/api/offers/${offerId}`);
    setO(fresh);
    if (fresh.status !== "sent") return;
    setReply(await post(`/api/offers/${offerId}/${what}`));
    load();
  };
  const hospitals = o.hospitals.map((h: any) => h.name);
  const title = o.kind === "start_early" ? "Can you start early?" : "Can you cover a split shift?";

  return (
    <>
      <div className="offer-head">Sevaro coverage request</div>
      <div className="offer-title">{title}</div>
      <div className="offer-row"><div className="k">Hospitals</div><div className="v">{hospitals.join(", ")}</div></div>
      <div className="offer-row"><div className="k">Hours</div><div className="v">{o.window_label}</div></div>
      <div className="offer-row"><div className="k">Start</div><div className="v">{fmtTime(o.start)} ET</div></div>
      <div className="offer-row"><div className="k">Why you</div><div className="v" style={{ fontSize: 15 }}>{whyYou(o.message)}</div></div>

      {reply && !reply.ok && <div className="phone-msg">{reply.message}</div>}

      {o.status === "sent" && (
        <>
          <div className="countdown">{mmss(o.seconds_left)}</div>
          <div className="countdown-sub">left to reply</div>
          <div className="phone-buttons">
            <button className="btn primary" onClick={() => respond("accept")}>Accept</button>
            <button className="btn" onClick={() => respond("decline")}>Decline</button>
            <PlayOffer text={o.message} offerId={o.id} />
          </div>
        </>
      )}
      {o.status === "accepted" && (
        <div style={{ marginTop: 16 }}>
          <div className="phone-msg" style={{ padding: "10px 0" }}>
            You are confirmed. Coverage starts {fmtTime(o.online_at)} ET.
          </div>
          <div className="small"><b>Before you start:</b></div>
          <ul className="small">
            <li>Open Synapse and sign in.</li>
            {hospitals.map((h: string) => <li key={h}>Check you can see the {h} queue.</li>)}
            <li>Keep your phone on for hospital calls.</li>
          </ul>
          <div className="phone-buttons">
            {o.online_confirmed_at ? (
              <div className="chip ok" style={{ textAlign: "center", padding: 10 }}>Online since {fmtTime(o.online_confirmed_at)}</div>
            ) : (
              <button className="btn primary" onClick={() => post(`/api/offers/${offerId}/online`).then(load)}>I'm online</button>
            )}
            <button className="btn" onClick={() => download(`relay-${o.id}.ics`, icsFor(o))}>Add to calendar (Outlook)</button>
          </div>
        </div>
      )}
      {o.status === "declined" && <div className="phone-msg">You declined. Thank you for letting us know.</div>}
      {o.status === "expired" && <div className="phone-msg">This offer expired. Thank you.</div>}
      {(o.status === "filled" || o.status === "cancelled") && (
        <div className="phone-msg">This shift is already filled. Thank you.</div>
      )}
    </>
  );
}

function whyYou(message: string): string {
  const s = message.split("You were asked because ")[1]?.split(". Please")[0] || "";
  return s ? s[0].toUpperCase() + s.slice(1) + "." : "";
}

// Reads the guarded offer text aloud: ElevenLabs voice when configured on the server, else the browser voice.
function PlayOffer({ text, offerId }: { text: string; offerId: string }) {
  const play = async () => {
    const res = await fetch(`/api/offers/${offerId}/voice`);
    if (res.ok) {
      const url = URL.createObjectURL(await res.blob());
      new Audio(url).play();
    } else if ("speechSynthesis" in window) {
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
    }
  };
  return <button className="btn ghost" onClick={play}>Play offer aloud</button>;
}
