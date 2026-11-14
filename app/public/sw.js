/* ScamShield service worker: app shell offline + Web Push.
   Never caches /api, /cloud or websockets: live call data and money stay live. */
const CACHE = "scamshield-v1";
const SHELL = ["/", "/guardian", "/manifest.webmanifest", "/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (/^\/(api|cloud|ws|cloud-ws|twilio)(\/|$)/.test(url.pathname)) return;
  if (e.request.mode === "navigate") {
    // network first, fall back to the cached shell so the app opens offline
    e.respondWith(
      fetch(e.request)
        .then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put("/", copy)); return r; })
        .catch(() => caches.match("/").then((r) => r || caches.match("/guardian"))),
    );
    return;
  }
  // hashed build assets, fonts, icons: cache first
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
      if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
      return r;
    })),
  );
});

self.addEventListener("push", (e) => {
  let d = { title: "ScamShield", body: "Open ScamShield", url: "/guardian" };
  try { d = { ...d, ...e.data.json() }; } catch (_) {}
  e.waitUntil(self.registration.showNotification(d.title, {
    body: d.body, icon: "/icons/icon-192.png", badge: "/icons/icon-192.png", data: { url: d.url }, tag: "scamshield",
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/guardian";
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((list) => {
    for (const c of list) if ("focus" in c) { c.navigate(url); return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
