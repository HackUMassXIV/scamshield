import { cloud, post } from "./api";
import type { Settings } from "./config";

export function registerServiceWorker() {
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

const b64ToBytes = (b64: string) => {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

/** Returns a sentence describing what happened, for the Settings sheet. */
export async function enablePush(s: Settings): Promise<string> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window))
    return "This browser can't receive notifications here. On iPhone, add ScamShield to your Home Screen first, then open it from there.";
  const { enabled, public_key } = await cloud<{ enabled: boolean; public_key: string }>(s, "/api/push/key");
  if (!enabled || !public_key) return "Notifications aren't set up on the server yet (VAPID keys missing). The app still updates live while open.";
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return "Notifications are blocked. Allow them in your browser settings to get alerts.";
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(public_key) });
  await cloud(s, "/api/push/subscribe", post({ user_id: s.userId, subscription: sub.toJSON() }));
  return "Notifications are on. You'll be alerted when a call turns red or a payment is held.";
}
