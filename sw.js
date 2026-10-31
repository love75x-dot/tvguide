// 앱 설치·오프라인용 서비스 워커
// - 화면(index.html, 아이콘): 인터넷 먼저, 안 되면 저장본
// - 데이터(data/*.json): 항상 인터넷에서 최신, 끊기면 마지막 저장본
const CACHE = "tvguide-v3";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const key = url.pathname.startsWith("/data/") ? url.pathname : e.request;   // ?t= 붙은 데이터도 한 칸에 저장
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(key, copy)); }
      return res;
    }).catch(() => caches.match(key).then(r => r || caches.match("index.html")))
  );
});
