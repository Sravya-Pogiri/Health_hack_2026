import { useCallback, useEffect, useState } from "react";
import { api, post } from "../api";
import { download, fmtTime, icsFor, mmss } from "../util";
import Icon from "./Icon";

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
    <div className="phone-wrap">
      <div style={{ width: 300 }}>
        <div className="section-title">Doctors with offers</div>
        {docs.length === 0 && <p className="muted">No offers yet. Approve one in the clinical lead view.</p>}
        {docs.map((d) => (
          <div key={d} className={`inbox-item ${d === current ? "active" : ""}`} onClick={() => { setDoc(d); setOfferId(null); }}>
            <b>{byDoc[d][0].physician_name}</b>
            <div className="muted small">{byDoc[d].map((o: any) => `${o.id}: ${o.status}`).join(", ")}</div>
          </div>
        ))}
        {offers.length > 1 && (
          <>
            <div className="section-title">Offers for this doctor</div>
            {offers.map((o: any) => (
              <div key={o.id} className={`inbox-item ${shown && o.id === shown.id ? "active" : ""}`} onClick={() => setOfferId(o.id)}>
                {o.id}: {o.window_label} ({o.status})
              </div>
            ))}
          </>
        )}
        {shown && (
          <p className="small muted">
            Real-phone link: <a href={`/offer/${shown.id}`} target="_blank" rel="noreferrer">/offer/{shown.id}</a>.
            Delivered by {shown.channel_label}
            {shown.delivery?.status && shown.delivery.status !== "simulated" ? ` (${shown.delivery.status}).` : " (simulated)."}
            {shown.channel === "sms" && " A text reply of 1 accepts and 2 declines."}
          </p>
        )}
      </div>
      <div className="phone">
        <div className="phone-screen">
          {shown ? <OfferScreen offerId={shown.id} /> : (
            <div className="phone-empty">
              <span className="ring"><Icon name="phone" size={28} /></span>
              <b>No offers yet</b>
              <span className="small">When the clinical lead approves an offer, it arrives here with a 10-minute countdown.</span>
            </div>
          )}
        </div>
      </div>
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
