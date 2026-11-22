/* Roster Monitor - site-wide display time zone (GMT or EST).
 *
 * Display layer only. The league pages keep formatting times as GMT ("21/11/2026, 13:50:30 GMT"),
 * stored JSON timestamps are never touched, and CSV exports stay GMT (their columns say so).
 * This script rewrites those GMT texts on screen when EST is chosen, and back again:
 *   GMT = UTC (offset 0)
 *   EST = fixed UTC-5, all year (no switch to EDT)
 * New page content (renders, history loads, refreshes) is converted as it is added, through a
 * MutationObserver, so every page shows one zone consistently.
 *
 * Choice: localStorage "roster-monitor-timezone" = "GMT" | "EST"; missing or invalid -> GMT.
 * Markup it uses: <select data-tz-select> (the picker) and <span class="tz-label"> (zone name).
 */
(function () {
  'use strict'
  var KEY = 'roster-monitor-timezone'
  var OFFSET_MINUTES = { GMT: 0, EST: -300 }
  // en-GB "dd/mm/yyyy, hh:mm:ss" + zone, exactly as the pages' gmt() helpers print it
  var STAMP = /(\d{2})\/(\d{2})\/(\d{4}), (\d{2}):(\d{2}):(\d{2}) (GMT|EST)\b/g
  var FORMAT = { timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
                 hour: '2-digit', minute: '2-digit', second: '2-digit' }

  function valid(zone) { return Object.prototype.hasOwnProperty.call(OFFSET_MINUTES, zone) }

  var current = 'GMT'
  try {
    var saved = window.localStorage.getItem(KEY)
    if (valid(saved)) current = saved
  } catch (e) { /* storage unavailable: GMT */ }

  /** Absolute time (ms since epoch) shown in `zone`, e.g. "21/11/2026, 08:50:30 EST". */
  function format(value, zone) {
    zone = valid(zone) ? zone : current
    if (value === null || value === undefined || value === '') return '-'
    var ms = typeof value === 'number' ? value : new Date(value).getTime()
    if (isNaN(ms)) return String(value)
    // shift the instant by the zone's fixed offset, then print it as UTC: date changes included
    return new Date(ms + OFFSET_MINUTES[zone] * 60000).toLocaleString('en-GB', FORMAT) + ' ' + zone
  }

  /** The instant a displayed stamp stands for (ms), or NaN. */
  function parse(day, month, year, hour, minute, second, zone) {
    return Date.UTC(+year, +month - 1, +day, +hour, +minute, +second) - OFFSET_MINUTES[zone] * 60000
  }

  /** Text with every displayed stamp rewritten into `zone`. */
  function convertText(text, zone) {
    STAMP.lastIndex = 0
    if (!STAMP.test(text)) return text
    STAMP.lastIndex = 0
    return text.replace(STAMP, function (match, d, mo, y, h, mi, s, from) {
      return from === zone ? match : format(parse(d, mo, y, h, mi, s, from), zone)
    })
  }

  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1 }

  function convertNode(node) {
    if (node.nodeType === 3) {
      var next = convertText(node.nodeValue, current)
      if (next !== node.nodeValue) node.nodeValue = next
      return
    }
    if (node.nodeType !== 1 && node.nodeType !== 9 && node.nodeType !== 11) return
    if (node.nodeType === 1 && SKIP[node.nodeName]) return
    if (node.nodeType === 1 && node.classList && node.classList.contains('tz-label')) {
      if (node.textContent !== current) node.textContent = current
      return
    }
    var walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (n) {
        if (n.nodeType === 1) return SKIP[n.nodeName] ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP
        return NodeFilter.FILTER_ACCEPT
      }
    })
    var texts = []
    for (var t = walker.nextNode(); t; t = walker.nextNode()) texts.push(t)
    texts.forEach(convertNode)
    if (node.querySelectorAll) {
      var labels = node.querySelectorAll('.tz-label')
      for (var i = 0; i < labels.length; i++) if (labels[i].textContent !== current) labels[i].textContent = current
    }
  }

  function syncSelects() {
    var selects = document.querySelectorAll('select[data-tz-select]')
    for (var i = 0; i < selects.length; i++) if (selects[i].value !== current) selects[i].value = current
  }

  function apply(zone, persist) {
    if (!valid(zone)) zone = 'GMT'
    current = zone
    if (persist) {
      try { window.localStorage.setItem(KEY, zone) } catch (e) { /* not saved; still applied */ }
    }
    syncSelects()
    if (document.body) convertNode(document.body)
    if (document.documentElement) document.documentElement.setAttribute('data-timezone', zone)
  }

  // convert content as the page adds it (renders, history, refresh); our own rewrites produce
  // text already in the chosen zone, so they do not loop
  var observer = new MutationObserver(function (records) {
    for (var r = 0; r < records.length; r++) {
      var rec = records[r]
      if (rec.type === 'characterData') convertNode(rec.target)
      else for (var a = 0; a < rec.addedNodes.length; a++) convertNode(rec.addedNodes[a])
    }
  })
  observer.observe(document.documentElement, { childList: true, characterData: true, subtree: true })

  function ready() {
    var selects = document.querySelectorAll('select[data-tz-select]')
    for (var i = 0; i < selects.length; i++) {
      selects[i].addEventListener('change', function (e) { apply(e.target.value, true) })
    }
    apply(current, false)
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready)
  else ready()

  // another tab or league page changed the choice
  window.addEventListener('storage', function (e) {
    if (e.key === KEY) apply(valid(e.newValue) ? e.newValue : 'GMT', false)
  })

  window.RosterTime = { KEY: KEY, get: function () { return current }, set: function (z) { apply(z, true) },
                        format: format, convertText: convertText }
})()
