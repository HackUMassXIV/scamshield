// Settings live in localStorage (per device). Defaults assume the PWA is served by the
// edge through the tunnel, so the edge is same-origin and the cloud is reached via /cloud.
export type Settings = {
  userId: string;
  guardianKey: string;
  edgeUrl: string; // "" = same origin
  cloudUrl: string; // "" = via the edge proxy (/cloud); set only to talk to the cloud directly
  cloudKey: string; // only needed with a direct cloudUrl
  speakCoach: boolean; // read coaching aloud (use earbuds!)
};

const KEY = "scamshield.settings.v1";
const DEFAULTS: Settings = { userId: "alex", guardianKey: "", edgeUrl: "", cloudUrl: "", cloudKey: "", speakCoach: false };

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* private mode: settings last for this visit only */
  }
}

export const edgeBase = (s: Settings) => (s.edgeUrl || window.location.origin).replace(/\/$/, "");
export const cloudBase = (s: Settings) => (s.cloudUrl ? s.cloudUrl.replace(/\/$/, "") : `${edgeBase(s)}/cloud`);
const toWs = (u: string) => u.replace(/^http/, "ws");
export const edgeWs = (s: Settings) =>
  `${toWs(edgeBase(s))}/ws/ui?user_id=${encodeURIComponent(s.userId)}&key=${encodeURIComponent(s.guardianKey)}`;
export const cloudWs = (s: Settings) =>
  s.cloudUrl
    ? `${toWs(cloudBase(s))}/ws/cloud?user_id=${encodeURIComponent(s.userId)}&key=${encodeURIComponent(s.cloudKey)}`
    : `${toWs(edgeBase(s))}/cloud-ws?user_id=${encodeURIComponent(s.userId)}&key=${encodeURIComponent(s.guardianKey)}`;
