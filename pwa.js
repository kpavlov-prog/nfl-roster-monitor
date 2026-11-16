/* Registers the Roster Monitor service worker. Shared by every page.
 * The site root is taken from this script's own URL, so it works from / and from leagues/<league>/
 * and under the GitHub Pages base path (/nfl-roster-monitor/). Registering again on another page
 * reuses the same registration; it never creates a second one. */
(function () {
  if (!('serviceWorker' in navigator)) return
  var script = document.currentScript
  if (!script || !script.src) return
  var root = new URL('./', script.src)
  window.addEventListener('load', function () {
    navigator.serviceWorker
      .register(new URL('service-worker.js', root).href, { scope: root.pathname })
      .catch(function (error) {
        console.warn('Roster Monitor: service worker registration failed', error)
      })
  })
})()
