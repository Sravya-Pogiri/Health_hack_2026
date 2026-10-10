const TZ = "America/New_York";

export function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: TZ });
}

export function fmtTimeSec(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: TZ });
}

export function minutesBetween(a?: string | null, b?: string | null): number {
  if (!a || !b) return 0;
  return Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60000);
}

export function shortHospital(name: string): string {
  return name
    .replace(" Medical Center", "")
    .replace(" Community Hospital", "")
    .replace(" University Medical Center", "")
    .replace(" Regional Hospital", "")
    .replace(" Medical Pavilion", "")
    .replace(" Health Center", "")
    .replace(" Hospital", "")
    .replace(" Medical", "")
    .replace(" Regional", "");
}

export function mmss(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const SEVERITY_LABEL: Record<string, string> = {
  critical: "Critical",
  warning: "Warning",
  projected: "Coming up",
  info: "Info",
};

// Calendar invite (opens in Outlook or any calendar). Hospitals and hours only, no patient details.
export function icsFor(offer: any): string {
  const stamp = (iso: string) => new Date(iso).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const where = offer.hospitals.map((h: any) => h.name).join(", ");
  return [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Relay//Split shift//EN", "BEGIN:VEVENT",
    `UID:${offer.id}@relay`, `DTSTAMP:${stamp(offer.sent_at || offer.start)}`,
    `DTSTART:${stamp(offer.start)}`, `DTEND:${stamp(offer.end)}`,
    `SUMMARY:Sevaro split shift: ${where}`, `DESCRIPTION:Tele-neurology coverage for ${where}.`,
    "END:VEVENT", "END:VCALENDAR",
  ].join("\r\n");
}

export function download(name: string, text: string, type = "text/calendar") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
