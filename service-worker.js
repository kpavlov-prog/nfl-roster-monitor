/* Roster Monitor service worker.
 *
 * Scope: the folder this file is served from (on GitHub Pages: /nfl-roster-monitor/).
 * Every path below is relative to that folder, so nothing assumes the site is at a domain root.
 *
 * Strategy
 * - data/ and logs/ (live roster JSON and change logs): never intercepted and never cached here.
 *   The browser fetches them exactly as it would without a service worker, so rosters are never
 *   served stale by this file. Offline, those requests fail and the pages show their own error.
 * - Pages and static app files (HTML, manifest, icons, pwa.js): network first, so a new deploy is
 *   picked up on the next online visit; the cached copy is used only when the network fails.
 * - Other origins (team logos on ESPN etc.): not intercepted.
 */
const CACHE_PREFIX = 'roster-monitor-'
const CACHE_NAME = CACHE_PREFIX + 'shell-v6'
const SCOPE_PATH = new URL('./', self.location.href).pathname
const OFFLINE_PAGE = 'offline.html'

const SHELL = [
  './',
  'leagues/nfl/',
  'leagues/ncaaf/',
  'leagues/nba/',
  'leagues/wnba/',
  'leagues/mlb/',
  'leagues/nhl/',
  OFFLINE_PAGE,
  'pwa.js',
  'theme.css',
  'themes/ncaaf-stadium.svg',
  'themes/nfl-field.svg',
  'themes/basketball-court.svg',
  'themes/mlb-diamond.svg',
  'manifest.webmanifest',
  'icons/icon.svg',
  'icons/favicon-32.png',
  'icons/apple-touch-icon.png',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/icon-maskable-192.png',
  'icons/icon-maskable-512.png'
]

const LIVE_DATA_FOLDERS = ['data/', 'logs/']

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL.map(path => new Request(path, { cache: 'reload' }))))
      .then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
        .map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  )
})

function isLiveData(url) {
  const path = url.pathname.slice(SCOPE_PATH.length)
  return LIVE_DATA_FOLDERS.some(folder => path.startsWith(folder))
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME)
  try {
    const response = await fetch(request)
    if (response.ok && response.type === 'basic') {
      cache.put(request, response.clone())
    }
    return response
  } catch (error) {
    const cached = await cache.match(request, { ignoreSearch: true })
    if (cached) return cached
    if (request.mode === 'navigate') {
      const offline = await cache.match(OFFLINE_PAGE)
      if (offline) return offline
    }
    return new Response('Offline', { status: 503, statusText: 'Offline', headers: { 'Content-Type': 'text/plain' } })
  }
}

self.addEventListener('fetch', event => {
  const request = event.request
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (!url.pathname.startsWith(SCOPE_PATH)) return
  if (isLiveData(url)) return

  event.respondWith(networkFirst(request))
})
