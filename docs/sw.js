// Lets the installed app open offline. Always tries the network first so you
// see fresh data; the saved copy is only used when there's no connection.
const CACHE = 'flight-risk-v2';
const SHELL = ['./', 'index.html', 'history.html', 'latest.json', 'history.json', 'nacl-fast.min.js',
               'manifest.webmanifest', 'icons/icon-192.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return; // GitHub API, webcams, maps: straight to network
  e.respondWith(
    fetch(e.request).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
      return r;
    }).catch(() => caches.match(e.request, {ignoreSearch: true}))
  );
});
