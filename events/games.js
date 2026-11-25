/*
 * Games page logic: schedule data -> days -> filtered rows, pins, and live overlays from ESPN.
 *
 * Data paths
 *   schedule  data/events/upcoming.json (written by scripts/check_upcoming_events.py every 6 hours)
 *   live      the browser asks ESPN's league scoreboard directly (CORS: *), one request per live league
 *             and ESPN date - never one request per game - and only while games are live or about to start.
 *
 * Times: stored as UTC; shown in the site's zone (RosterTime: GMT, or fixed EST = UTC-5). Days are
 * cut in that zone, so a 01:00 UTC game belongs to the previous day in EST. Never the browser's zone.
 *
 * Everything that comes from ESPN is treated as untrusted text: rows are built with createElement /
 * textContent, never innerHTML.
 *
 * Pure parts (validate, dayKey, filters, pins, live merge, poll planning) are exported for Node tests.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory()
  else root.Games = factory()
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict'

  var SITE = 'https://site.api.espn.com/apis/site/v2/sports'
  var LEAGUE_API = {
    nfl: { path: 'football/nfl', params: '' }, ncaaf: { path: 'football/college-football', params: '&groups=80&limit=400' },
    nba: { path: 'basketball/nba', params: '' }, wnba: { path: 'basketball/wnba', params: '' },
    ncaab: { path: 'basketball/mens-college-basketball', params: '&groups=50&limit=500' },
    mlb: { path: 'baseball/mlb', params: '' }, nhl: { path: 'hockey/nhl', params: '' }
  }
  var PIN_KEY = 'roster-monitor-pinned-events'
  var EVENT_ID = /^\d{1,15}$/
  var DAY = 86400000

  // polling (one controller for the page)
  var POLL = {
    pinnedLiveMs: 15000,     // a pinned game is live
    liveMs: 25000,           // some game is live / about to start
    idleCheckMs: 60000,      // nothing live: re-check the plan locally (no network)
    hiddenMs: 300000,        // tab hidden: at most one refresh every 5 minutes
    soonMs: 10 * 60000,      // a game starting within 10 minutes counts as about to start
    lateMs: 8 * 3600000,     // a "pre" game up to 8 hours past its start may still begin / be live
    staleAfterMs: 90000      // a live league not refreshed for this long is marked "may be stale"
  }

  // ---------------------------------------------------------------- validation
  function str(v, max) { return v === null || v === undefined ? '' : String(v).slice(0, max || 120) }
  function num(v) { return typeof v === 'number' && isFinite(v) && v >= 0 && v < 1000 ? Math.round(v) : null }
  function team(t) {
    if (!t || typeof t !== 'object') return null
    return { id: str(t.id, 12), code: str(t.code, 10), name: str(t.name, 80) || str(t.code, 10) || 'TBD', shortName: str(t.shortName, 40) || str(t.name, 40),
             logo: /^https:\/\//.test(str(t.logo, 300)) ? str(t.logo, 300) : '', monitored: t.monitored === true }
  }
  /** A stored event, checked field by field; null if it cannot be shown safely (one bad event never breaks the page). */
  function validate(e) {
    if (!e || typeof e !== 'object') return null
    var id = str(e.eventId, 20), ms = Date.parse(e.startTimeUTC)
    if (!EVENT_ID.test(id) || isNaN(ms) || !LEAGUE_API[e.leagueKey]) return null
    var home = team(e.homeTeam), away = team(e.awayTeam), st = e.status || {}
    if (!home || !away || ['pre', 'in', 'post'].indexOf(st.state) < 0) return null
    var gs = e.gameState || {}, sc = e.score || {}
    return {
      eventId: id, leagueKey: e.leagueKey, league: str(e.league, 10), sport: str(e.sport, 20), startMs: ms, espnDate: /^\d{8}$/.test(e.espnDate) ? e.espnDate : '',
      home: home, away: away, venue: str(e.venue, 100), neutralSite: e.neutralSite === true,
      state: st.state, detail: str(st.detail, 60), completed: st.completed === true, postponed: st.postponed === true, cancelled: st.cancelled === true, delayed: st.delayed === true,
      score: { home: num(sc.home), away: num(sc.away) },
      period: num(gs.period), clock: gs.clock ? str(gs.clock, 10) : null, display: gs.display ? str(gs.display, 60) : null,
      live: null     // set by the live overlay: { at: ms }
    }
  }
  function load(doc) {
    var out = [], bad = 0, seen = {}
    var list = doc && Array.isArray(doc.events) ? doc.events : []
    for (var i = 0; i < list.length; i++) {
      var ev = validate(list[i])
      if (!ev) { bad++; continue }
      if (seen[ev.eventId]) continue           // event id is the identity: never two rows for one game
      seen[ev.eventId] = true; out.push(ev)
    }
    return { events: out, skipped: bad }
  }

  // ---------------------------------------------------------------- days and times in the chosen zone
  function pad(n) { return (n < 10 ? '0' : '') + n }
  function shifted(ms, offsetMin) { return new Date(ms + offsetMin * 60000) }
  /** "YYYY-MM-DD" of instant `ms` in a fixed-offset zone. */
  function dayKey(ms, offsetMin) { var d = shifted(ms, offsetMin); return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) }
  function timeLabel(ms, offsetMin) { var d = shifted(ms, offsetMin); return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) }
  function addDays(key, n) { var p = key.split('-'); var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]) + n * DAY); return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) }
  var WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  function dayParts(key) { var p = key.split('-'); var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])); return { weekday: WD[d.getUTCDay()], day: d.getUTCDate(), month: MON[d.getUTCMonth()] } }
  /** The 9 days the page offers: yesterday .. today+7, in the chosen zone. */
  function dayList(nowMs, offsetMin) { var t = dayKey(nowMs, offsetMin), out = []; for (var i = -1; i <= 7; i++) out.push(addDays(t, i)); return out }

  // ---------------------------------------------------------------- classification, filters, order
  /** live | upcoming | finished | off (postponed / cancelled) */
  function section(ev) {
    if (ev.postponed || ev.cancelled) return 'off'
    if (ev.state === 'in') return 'live'
    if (ev.state === 'post') return 'finished'
    return 'upcoming'
  }
  var SECTION_ORDER = ['live', 'upcoming', 'finished', 'off']
  function byTime(a, b) { return a.startMs - b.startMs || (a.league < b.league ? -1 : a.league > b.league ? 1 : 0) || (a.eventId < b.eventId ? -1 : a.eventId > b.eventId ? 1 : 0) }
  function sportOfLeague(groups, key) { for (var i = 0; i < groups.length; i++) for (var j = 0; j < groups[i].leagues.length; j++) if (groups[i].leagues[j].key === key) return groups[i].id; return null }
  /**
   * Rows for the current view.
   * f = { day, offset, sport ('all' | group id), league ('all' | league key), mode ('all' | 'live' | 'pinned'), pins: {id:true}, groups }
   * 'all' mode: the selected day only. 'live' / 'pinned' modes: every loaded day (few rows), grouped by day.
   */
  function select(events, f) {
    var out = []
    for (var i = 0; i < events.length; i++) {
      var ev = events[i]
      if (f.league !== 'all' && ev.leagueKey !== f.league) continue
      if (f.sport !== 'all' && sportOfLeague(f.groups, ev.leagueKey) !== f.sport) continue
      if (f.mode === 'live' && section(ev) !== 'live') continue
      if (f.mode === 'pinned' && !f.pins[ev.eventId]) continue
      if (f.mode === 'all' && dayKey(ev.startMs, f.offset) !== f.day) continue
      out.push(ev)
    }
    return out.sort(byTime)
  }
  /** [{section, events}] in the order LIVE, UPCOMING, FINISHED, POSTPONED/CANCELLED; chronological inside. */
  function sections(rows) {
    var buckets = { live: [], upcoming: [], finished: [], off: [] }
    rows.forEach(function (ev) { buckets[section(ev)].push(ev) })
    return SECTION_ORDER.filter(function (s) { return buckets[s].length }).map(function (s) { return { section: s, events: buckets[s] } })
  }
  function countsByDay(events, f, days) {
    var c = {}; days.forEach(function (d) { c[d] = 0 })
    var g = { day: null, offset: f.offset, sport: f.sport, league: f.league, mode: 'any', pins: f.pins, groups: f.groups }
    for (var i = 0; i < events.length; i++) {
      var ev = events[i], k = dayKey(ev.startMs, f.offset)
      if (!(k in c)) continue
      if (f.league !== 'all' && ev.leagueKey !== f.league) continue
      if (f.sport !== 'all' && sportOfLeague(f.groups, ev.leagueKey) !== f.sport) continue
      c[k]++
    }
    return c
  }

  // ---------------------------------------------------------------- pins (localStorage, ids only)
  /** {eventId: pinnedAtMs}. Corrupted or foreign values recover to {} (never throws). */
  function readPins(storage) {
    try {
      var raw = storage.getItem(PIN_KEY)
      if (!raw) return {}
      var v = JSON.parse(raw), out = {}
      if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
      Object.keys(v).forEach(function (k) { if (EVENT_ID.test(k) && typeof v[k] === 'number' && isFinite(v[k])) out[k] = v[k] })
      return out
    } catch (e) { return {} }
  }
  function writePins(storage, pins) { try { storage.setItem(PIN_KEY, JSON.stringify(pins)); return true } catch (e) { return false } }
  function togglePin(pins, id, nowMs) { var p = Object.assign({}, pins); if (p[id]) delete p[id]; else p[id] = nowMs; return p }
  /** Drop pins that are no longer in the loaded schedule AND were pinned more than 21 days ago (a finished pinned game stays pinned). */
  function prunePins(pins, events, nowMs) {
    var present = {}; events.forEach(function (e) { present[e.eventId] = true })
    var out = {}; Object.keys(pins).forEach(function (k) { if (present[k] || nowMs - pins[k] < 21 * DAY) out[k] = pins[k] })
    return out
  }

  // ---------------------------------------------------------------- live overlay
  function scoreboardUrl(leagueKey, espnDate) { var a = LEAGUE_API[leagueKey]; return SITE + '/' + a.path + '/scoreboard?dates=' + espnDate + a.params }
  /** Live fields of one raw ESPN scoreboard event, validated; null if unusable. */
  function liveFields(raw) {
    if (!raw || typeof raw !== 'object' || !EVENT_ID.test(str(raw.id, 20))) return null
    var comp = (raw.competitions || [])[0] || {}, cs = comp.competitors || []
    var st = raw.status || comp.status || {}, typ = st.type || {}
    if (['pre', 'in', 'post'].indexOf(typ.state) < 0) return null
    var sides = {}; cs.forEach(function (c) { if (c && (c.homeAway === 'home' || c.homeAway === 'away')) sides[c.homeAway] = c })
    if (cs.length !== 2 || !sides.home || !sides.away) return null                  // home/away only from ESPN's flags
    var name = str(typ.name, 40).toUpperCase()
    function sc(c) { if (typ.state === 'pre') return null; var n = parseFloat(c.score && typeof c.score === 'object' ? c.score.value : c.score); return isFinite(n) && n >= 0 && n < 1000 ? Math.round(n) : null }
    return {
      id: str(raw.id, 20), state: typ.state, detail: str(typ.shortDetail || typ.detail, 60),
      postponed: name.indexOf('POSTPONED') >= 0, cancelled: name.indexOf('CANCEL') >= 0, delayed: name.indexOf('DELAY') >= 0 || name.indexOf('RAIN') >= 0,
      completed: typ.completed === true, homeId: str((sides.home.team || {}).id, 12), awayId: str((sides.away.team || {}).id, 12),
      score: { home: sc(sides.home), away: sc(sides.away) },
      period: typeof st.period === 'number' && st.period >= 0 && st.period < 50 ? st.period : null,
      clock: typ.state === 'in' ? str(st.displayClock, 10) || null : null
    }
  }
  /**
   * Overlay live values onto a stored event (matched by event id). Stable fields (teams, start, venue) are
   * kept. A live answer that names other teams for this id is ignored. Returns true if anything changed.
   */
  function merge(ev, live, atMs) {
    if (!live || live.id !== ev.eventId) return false
    if ((ev.home.id && live.homeId && ev.home.id !== live.homeId) || (ev.away.id && live.awayId && ev.away.id !== live.awayId)) return false
    var before = JSON.stringify([ev.state, ev.detail, ev.score, ev.period, ev.clock, ev.postponed, ev.cancelled, ev.delayed])
    ev.state = live.state; ev.detail = live.detail || ev.detail; ev.completed = live.completed
    ev.postponed = live.postponed; ev.cancelled = live.cancelled; ev.delayed = live.delayed
    // a missing score in the live answer never blanks a score we already show
    ev.score = { home: live.score.home !== null ? live.score.home : ev.score.home, away: live.score.away !== null ? live.score.away : ev.score.away }
    ev.period = live.period !== null ? live.period : ev.period
    ev.clock = live.clock; ev.display = live.detail || ev.display
    ev.live = { at: atMs }
    return JSON.stringify([ev.state, ev.detail, ev.score, ev.period, ev.clock, ev.postponed, ev.cancelled, ev.delayed]) !== before
  }
  /** Is this game live now or about to start (so its league scoreboard is worth asking)? */
  function relevant(ev, nowMs) {
    if (ev.postponed || ev.cancelled) return false
    if (ev.state === 'in') return true
    if (ev.state === 'pre') return ev.startMs <= nowMs + POLL.soonMs && ev.startMs >= nowMs - POLL.lateMs
    return false
  }
  /** { leagueKey: [espnDate, ...] } to request, and the interval to use. One request per league per ESPN date. */
  function plan(events, nowMs, pins) {
    var leagues = {}, anyPinnedLive = false, count = 0
    events.forEach(function (ev) {
      if (!relevant(ev, nowMs) || !ev.espnDate) return
      var l = leagues[ev.leagueKey] || (leagues[ev.leagueKey] = [])
      if (l.indexOf(ev.espnDate) < 0) l.push(ev.espnDate)
      if (pins && pins[ev.eventId] && ev.state === 'in') anyPinnedLive = true
      count++
    })
    Object.keys(leagues).forEach(function (k) { leagues[k].sort() })
    var any = Object.keys(leagues).length > 0
    return { leagues: leagues, relevantGames: count, intervalMs: !any ? null : anyPinnedLive ? POLL.pinnedLiveMs : POLL.liveMs }
  }

  /**
   * One polling controller for the page (no timer per game).
   * deps: { now(), fetchJson(url) -> Promise, setTimer(fn, ms) -> handle, clearTimer(handle), hidden() -> bool,
   *         events() -> current events, pins() -> pins, onUpdate(info) }
   */
  function Poller(deps) {
    this.d = deps; this.timer = null; this.running = false; this.inFlight = false
    this.leagueState = {}          // key -> { lastOk, lastError, failures }
    this.requests = 0
  }
  Poller.prototype.start = function () { this.running = true; this.schedule(0) }
  Poller.prototype.stop = function () { this.running = false; if (this.timer !== null) this.d.clearTimer(this.timer); this.timer = null }
  Poller.prototype.schedule = function (ms) {
    var self = this
    if (this.timer !== null) this.d.clearTimer(this.timer)
    this.timer = this.d.setTimer(function () { self.timer = null; self.tick() }, ms)
  }
  /** Visible again: refresh live leagues at once. */
  Poller.prototype.wake = function () { if (this.running) this.schedule(0) }
  Poller.prototype.tick = function () {
    var self = this
    if (!this.running) return Promise.resolve()
    var now = this.d.now(), p = plan(this.d.events(), now, this.d.pins())
    if (!p.intervalMs) { this.d.onUpdate({ plan: p, changed: 0 }); this.schedule(POLL.idleCheckMs); return Promise.resolve() }
    if (this.d.hidden()) {                          // hidden tab: no rapid polling
      var lastAny = Math.max.apply(null, [0].concat(Object.keys(this.leagueState).map(function (k) { return self.leagueState[k].lastOk || 0 })))
      if (now - lastAny < POLL.hiddenMs) { this.schedule(POLL.hiddenMs); return Promise.resolve() }
    }
    if (this.inFlight) { this.schedule(p.intervalMs); return Promise.resolve() }
    this.inFlight = true
    var jobs = []
    Object.keys(p.leagues).forEach(function (key) {
      p.leagues[key].forEach(function (date) {
        self.requests++
        jobs.push(self.d.fetchJson(scoreboardUrl(key, date)).then(function (data) {
          var byId = {}, list = data && Array.isArray(data.events) ? data.events : null
          if (!list) throw new Error('no events list')
          list.forEach(function (raw) { var l = liveFields(raw); if (l) byId[l.id] = l })
          var at = self.d.now(), changed = 0
          self.d.events().forEach(function (ev) { if (ev.leagueKey === key && byId[ev.eventId] && merge(ev, byId[ev.eventId], at)) changed++ })
          var s = self.leagueState[key] || (self.leagueState[key] = {}); s.lastOk = at; s.lastError = null; s.failures = 0
          return changed
        }).catch(function (err) {
          var s = self.leagueState[key] || (self.leagueState[key] = {}); s.lastError = String(err && err.message || err).slice(0, 120); s.failures = (s.failures || 0) + 1
          return 0                                   // keep every value already shown
        }))
      })
    })
    return Promise.all(jobs).then(function (changes) {
      self.inFlight = false
      var changed = changes.reduce(function (a, b) { return a + b }, 0)
      self.d.onUpdate({ plan: plan(self.d.events(), self.d.now(), self.d.pins()), changed: changed })
      var next = plan(self.d.events(), self.d.now(), self.d.pins())
      self.schedule(next.intervalMs || POLL.idleCheckMs)
    })
  }
  /** Leagues polled now whose last good answer is older than staleAfterMs (or never came). */
  Poller.prototype.stale = function (nowMs, p) {
    var self = this
    return Object.keys(p.leagues).filter(function (k) { var s = self.leagueState[k]; return s && s.failures > 0 && (!s.lastOk || nowMs - s.lastOk > POLL.staleAfterMs) })
  }

  // ---------------------------------------------------------------- query parameters
  function parseQuery(search, groups, days) {
    var q = {}; try { new URLSearchParams(search).forEach(function (v, k) { q[k.toLowerCase()] = v }) } catch (e) { /* ignore */ }
    var out = { sport: 'all', league: 'all', mode: 'all', day: null }
    var leagues = {}; groups.forEach(function (g) { g.leagues.forEach(function (l) { leagues[l.key] = g.id; leagues[l.label.toLowerCase()] = g.id }) })
    var lg = (q.league || '').toLowerCase()
    if (leagues[lg]) { out.league = groups.reduce(function (a, g) { return a || g.leagues.filter(function (l) { return l.key === lg || l.label.toLowerCase() === lg }).map(function (l) { return l.key })[0] }, null); out.sport = leagues[lg] }
    else if (groups.some(function (g) { return g.id === (q.sport || '').toLowerCase() })) out.sport = q.sport.toLowerCase()
    if (q.filter === 'live' || q.filter === 'pinned') out.mode = q.filter
    if (/^\d{4}-\d{2}-\d{2}$/.test(q.date || '') && days.indexOf(q.date) >= 0) out.day = q.date
    return out
  }

  return {
    POLL: POLL, PIN_KEY: PIN_KEY, LEAGUE_API: LEAGUE_API, validate: validate, load: load, dayKey: dayKey, timeLabel: timeLabel, addDays: addDays,
    dayParts: dayParts, dayList: dayList, section: section, select: select, sections: sections, countsByDay: countsByDay,
    readPins: readPins, writePins: writePins, togglePin: togglePin, prunePins: prunePins,
    scoreboardUrl: scoreboardUrl, liveFields: liveFields, merge: merge, relevant: relevant, plan: plan, Poller: Poller, parseQuery: parseQuery
  }
})
