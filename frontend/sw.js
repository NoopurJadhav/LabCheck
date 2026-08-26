const CACHE_NAME = "labcheck-shell-v1";
const SHELL_FILES = ["./index.html", "./app.js", "./manifest.json", "./icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
});

// Cache-first for the app shell; network for API calls (never cache live data).
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return; // let API calls hit network
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
