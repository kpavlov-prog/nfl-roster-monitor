/*
 * Shared grouped navigation for the league pages and Player Lookup.
 *
 * Usage (right where the nav belongs; the script must directly follow its <nav>):
 *   <nav class="top-nav" aria-label="Roster Monitor" data-nav-root="../../" data-nav-current="nba">
 *     <a class="nav-card" href="../../">Sports Hub</a> <a class="nav-card" href="../../lookup/">Player Lookup</a>
 *   </nav>
 *   <script src="../../nav.js"></script>
 *
 * The links inside <nav> are only a fallback for when this file cannot load; they are replaced
 * synchronously (before first paint), so the page does not shift. Sport menus are disclosure
 * buttons (aria-expanded / aria-controls): click or Enter/Space toggles, Escape or a click outside
 * closes, only one menu is open at a time. Colours come from the page/theme (--blue / --purple),
 * so every league keeps its own accent.
 *
 * GROUPS is the single list of sports and leagues; tests/test_nav.mjs checks that the Sports Hub
 * (index.html) shows the same groups.
 */
(function () {
  'use strict'
  var GROUPS = [
    { id: 'football', label: 'American Football', leagues: [
      { key: 'nfl', label: 'NFL', name: 'National Football League' },
      { key: 'ncaaf', label: 'NCAAF', name: 'College Football' }] },
    { id: 'basketball', label: 'Basketball', leagues: [
      { key: 'nba', label: 'NBA', name: 'National Basketball Association' },
      { key: 'wnba', label: 'WNBA', name: "Women's National Basketball Association" },
      { key: 'ncaab', label: 'NCAAB', name: "Men's College Basketball" }] },
    { id: 'baseball', label: 'Baseball', leagues: [
      { key: 'mlb', label: 'MLB', name: 'Major League Baseball' }] },
    { id: 'hockey', label: 'Ice Hockey', leagues: [
      { key: 'nhl', label: 'NHL', name: 'National Hockey League' }] }
  ]
  if (typeof module === 'object' && module.exports) { module.exports = { GROUPS: GROUPS }; return }

  var nav = document.currentScript && document.currentScript.previousElementSibling
  if (!nav || !nav.matches('nav[data-nav-root]')) return
  var base = nav.getAttribute('data-nav-root') || './'
  var current = nav.getAttribute('data-nav-current') || ''

  var CSS = [
    '.top-nav{position:relative;align-items:stretch}',
    '.top-nav .nav-card{display:inline-flex;align-items:center;gap:8px;min-height:44px;margin:0;padding:10px 14px;border-radius:14px;',
    '  border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.1);color:#fff;font:800 16px/1.2 Arial,sans-serif;text-decoration:none;cursor:pointer;white-space:nowrap}',
    '.top-nav .nav-card:hover{background:rgba(255,255,255,.18)}',
    '.top-nav .nav-card.active{background:linear-gradient(135deg,var(--blue),var(--purple));border-color:rgba(255,255,255,.28)}',
    '.top-nav .nav-card:focus-visible,.top-nav .nav-menu-link:focus-visible{outline:3px solid #fff;outline-offset:2px}',
    '.nav-group{position:relative;display:flex}',
    '.nav-current{display:inline-block;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.24);font-size:12px;font-weight:900;letter-spacing:.02em}',
    '.nav-caret{font-size:11px;transition:transform .15s ease}',
    '.nav-group-btn[aria-expanded="true"] .nav-caret{transform:rotate(180deg)}',
    '.nav-sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}',
    '.nav-menu{position:absolute;top:calc(100% + 6px);left:0;z-index:60;min-width:250px;max-width:calc(100vw - 16px);padding:6px;border-radius:14px;',
    '  background:#fff;color:#111827;border:1px solid #e5e7eb;box-shadow:0 18px 40px rgba(0,0,0,.35)}',
    '.nav-menu[hidden]{display:none}',
    '.nav-menu-link{display:flex;flex-direction:column;justify-content:center;gap:2px;min-height:48px;padding:8px 12px 8px 14px;border-radius:10px;',
    '  border-left:4px solid transparent;color:#111827;text-decoration:none;font-family:Arial,sans-serif}',
    '.nav-menu-link+.nav-menu-link{margin-top:2px}',
    '.nav-menu-link:hover{background:#f1f5f9}',
    '.nav-menu-link b{font-size:15px;font-weight:900}',
    '.nav-menu-link span{font-size:12px;color:#64748b;font-weight:700}',
    '.nav-menu-link.active{background:#f1f5f9;border-left-color:var(--blue)}',
    '.nav-menu-link.active b:after{content:"Current";margin-left:8px;padding:1px 7px;border-radius:999px;background:linear-gradient(135deg,var(--blue),var(--purple));color:#fff;font-size:10px;font-weight:900;vertical-align:2px}',
    '@media(max-width:600px){',
    '  .top-nav{display:grid;grid-template-columns:1fr 1fr;gap:8px}',
    '  .top-nav .nav-card{width:100%;justify-content:center;text-align:center;white-space:normal;padding:10px;gap:6px}',
    '  .nav-group-label{min-width:0}',
    '  .nav-current,.nav-caret{flex:none}',
    '  .nav-group{position:static}',
    '  .nav-menu{left:0;right:0;min-width:0;max-width:none}',
    '}',
    '@media print{.nav-menu{display:none!important}}'
  ].join('\n')
  if (!document.getElementById('nav-js-style')) {
    var style = document.createElement('style'); style.id = 'nav-js-style'; style.textContent = CSS
    document.head.appendChild(style)
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] }) }
  var html = ['<a class="nav-card nav-hub" href="' + esc(base) + '">Sports Hub</a>']
  GROUPS.forEach(function (g) {
    var here = g.leagues.filter(function (l) { return l.key === current })[0]
    html.push('<div class="nav-group" data-group="' + g.id + '">' +
      '<button type="button" class="nav-card nav-group-btn' + (here ? ' active' : '') + '" id="nav-btn-' + g.id + '" aria-expanded="false" aria-controls="nav-menu-' + g.id + '">' +
      '<span class="nav-group-label">' + esc(g.label) + '</span>' +
      (here ? '<span class="nav-current"><span class="nav-sr">current: </span>' + esc(here.label) + '</span>' : '') +
      '<span class="nav-caret" aria-hidden="true">▾</span></button>' +
      '<div class="nav-menu" id="nav-menu-' + g.id + '" hidden>' +
      g.leagues.map(function (l) {
        var on = l.key === current
        return '<a class="nav-menu-link' + (on ? ' active' : '') + '" href="' + esc(base) + 'leagues/' + l.key + '/"' + (on ? ' aria-current="page"' : '') + '>' +
          '<b>' + esc(l.label) + '</b><span>' + esc(l.name) + '</span></a>'
      }).join('') + '</div></div>')
  })
  var onLookup = current === 'lookup'
  html.push('<a class="nav-card nav-lookup' + (onLookup ? ' active' : '') + '" href="' + esc(base) + 'lookup/"' + (onLookup ? ' aria-current="page"' : '') + '>Player Lookup</a>')
  nav.innerHTML = html.join('')
  if (!nav.getAttribute('aria-label')) nav.setAttribute('aria-label', 'Roster Monitor')

  var groups = [].slice.call(nav.querySelectorAll('.nav-group'))
  function parts(g) { return { btn: g.querySelector('.nav-group-btn'), menu: g.querySelector('.nav-menu') } }
  function close(g, focusButton) {
    var p = parts(g)
    if (p.btn.getAttribute('aria-expanded') !== 'true') return
    p.btn.setAttribute('aria-expanded', 'false'); p.menu.hidden = true
    p.menu.style.transform = ''; p.menu.style.top = ''
    if (focusButton) p.btn.focus()
  }
  function closeAll() { groups.forEach(function (g) { close(g) }) }
  // keep the open menu inside the viewport (desktop: shift sideways; narrow screens: full nav width, under its button)
  function fit(p) {
    p.menu.style.transform = ''; p.menu.style.top = ''
    if (getComputedStyle(p.btn.parentNode).position === 'static') { p.menu.style.top = (p.btn.offsetTop + p.btn.offsetHeight + 6) + 'px'; return }
    var r = p.menu.getBoundingClientRect(), vw = document.documentElement.clientWidth, dx = 0
    if (r.right > vw - 8) dx = vw - 8 - r.right
    if (r.left + dx < 8) dx = 8 - r.left
    if (dx) p.menu.style.transform = 'translateX(' + Math.round(dx) + 'px)'
  }
  function open(g) {
    groups.forEach(function (x) { if (x !== g) close(x) })
    var p = parts(g)
    p.btn.setAttribute('aria-expanded', 'true'); p.menu.hidden = false; fit(p)
  }
  groups.forEach(function (g) {
    var p = parts(g)
    p.btn.addEventListener('click', function () { p.btn.getAttribute('aria-expanded') === 'true' ? close(g) : open(g) })
    // Tab past the last league link closes the menu
    g.addEventListener('focusout', function (e) { if (e.relatedTarget && !g.contains(e.relatedTarget)) close(g) })
  })
  // a click anywhere outside the open sport group (page or rest of the nav) closes it
  document.addEventListener('click', function (e) { groups.forEach(function (g) { if (!g.contains(e.target)) close(g) }) })
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return
    groups.forEach(function (g) { if (parts(g).btn.getAttribute('aria-expanded') === 'true') close(g, true) })
  })
  window.addEventListener('resize', closeAll)
})()
