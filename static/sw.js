// Mealie Mixer service worker — minimal: makes the app installable + works
// offline. NETWORK-FIRST (so updates always land when online); the cache is
// just the offline fallback. Never touches /api, /docs, /admin.

const CACHE = 'mealie-mixer-v7';
const SHELL = [
  '/', '/app.js', '/style.css', '/vendor/alpine.min.js',
  '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png',
  '/icons/brand.png', '/icons/favicon.png',
  '/vendor/fonts/outfit-latin.woff2', '/vendor/fonts/outfit-latin-ext.woff2',
  '/vendor/fonts/jakarta-latin.woff2', '/vendor/fonts/jakarta-latin-ext.woff2',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Web Share Target: the OS share sheet POSTs the shared image(s)/link here.
// The page can't read a POST body, so stash it in a cache and redirect the
// installed app to /?shared=1, where app.js picks it up.
async function handleShare(req) {
  try {
    const form = await req.formData();
    const cache = await caches.open('mm-share');
    const files = form.getAll('files').filter((f) => f && f.size);
    await cache.put('/__share_meta', new Response(JSON.stringify({
      text: form.get('text') || '', url: form.get('url') || '', count: files.length,
    }), { headers: { 'content-type': 'application/json' } }));
    for (let i = 0; i < files.length; i++) {
      await cache.put('/__share_file_' + i, new Response(files[i], {
        headers: { 'content-type': files[i].type || 'image/jpeg', 'x-filename': files[i].name || ('shared-' + i + '.jpg') },
      }));
    }
  } catch (_) { /* fall through to the app either way */ }
  return Response.redirect('/?shared=1', 303);
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const sUrl = new URL(req.url);
  if (req.method === 'POST' && sUrl.origin === location.origin && sUrl.pathname === '/share-target') {
    e.respondWith(handleShare(req));
    return;
  }
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/docs') || url.pathname.startsWith('/admin')) return;
  // network-first with REVALIDATION: `cache: 'no-cache'` makes the SW's own fetch
  // skip stale HTTP-cache hits (it still 304s when unchanged), so a fresh deploy of
  // app.js/index.html always lands without a manual hard-refresh. Offline still falls
  // back to the cached copy below.
  e.respondWith(
    fetch(req, { cache: 'no-cache' })
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('/')))
  );
});
