// Unit tests for events/games.js (Games page logic). Run: node --test tests/test_games.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const G = require('../events/games.js')
const { GROUPS } = require('../nav.js')

const T0 = Date.parse('2026-11-25T15:00:00Z')
const team = (id, code, name) => ({ id: String(id), code, name, shortName: name.split(' ').pop(), logo: `https://a.espncdn.com/${code}.png`, monitored: true })
function stored(id, league, start, state = 'pre', extra = {}) {
  return { eventId: String(id), league: league.toUpperCase(), leagueKey: league, sport: 'x', startTimeUTC: start, espnDate: start.slice(0, 10).replace(/-/g, ''),
    homeTeam: team(1, 'HOM', 'Home Team'), awayTeam: team(2, 'AWY', 'Away Team'), venue: 'Arena', neutralSite: false,
    status: { state, detail: state === 'post' ? 'Final' : '', completed: state === 'post', postponed: false, cancelled: false, delayed: false },
    score: { home: state === 'pre' ? null : 10, away: state === 'pre' ? null : 7 }, gameState: { period: null, clock: null, display: null }, ...extra }
}
const events = list => G.load({ events: list }).events
function espnEvent(id, state, home, away, { period = 2, clock = '4:21', detail = 'Q2 4:21', name = 'STATUS_IN_PROGRESS', homeId = '1', awayId = '2' } = {}) {
  return { id: String(id), status: { period, displayClock: clock, type: { name, state, completed: state === 'post', shortDetail: detail } },
    competitions: [{ competitors: [{ homeAway: 'away', team: { id: awayId }, score: String(away) }, { homeAway: 'home', team: { id: homeId }, score: String(home) }] }] }
}

test('validation: one malformed event is skipped, duplicates collapse to one id', () => {
  const r = G.load({ events: [stored(1, 'nba', '2026-11-25T23:00:00Z'), { eventId: 'x' }, null, stored(1, 'nba', '2026-11-25T23:00:00Z'),
    stored(2, 'bogus', '2026-11-25T23:00:00Z'), { ...stored(3, 'nba', 'not a time') }, stored(4, 'nhl', '2026-11-25T23:30:00Z')] })
  assert.deepEqual(r.events.map(e => e.eventId), ['1', '4'])
  assert.equal(r.skipped, 4)                        // bad id, null, unknown league, bad time (the duplicate is not an error)
  assert.deepEqual(G.load(null), { events: [], skipped: 0 })
})

test('validation: unsafe logos dropped; long strings bounded', () => {
  const e = stored(1, 'nba', '2026-11-25T23:00:00Z'); e.homeTeam.logo = 'javascript:alert(1)'; e.homeTeam.name = 'x'.repeat(500)
  const v = G.validate(e)
  assert.equal(v.home.logo, ''); assert.equal(v.home.name.length, 80)
})

test('days are cut in the chosen zone: GMT vs fixed EST, previous-day, month and year boundaries', () => {
  const ms = Date.parse('2026-11-26T01:00:00Z')
  assert.equal(G.dayKey(ms, 0), '2026-11-26'); assert.equal(G.dayKey(ms, -300), '2026-11-25')
  assert.equal(G.timeLabel(ms, 0), '01:00'); assert.equal(G.timeLabel(ms, -300), '20:00')
  assert.equal(G.dayKey(Date.parse('2026-12-01T03:00:00Z'), -300), '2026-11-30')     // month boundary
  assert.equal(G.dayKey(Date.parse('2027-01-01T04:59:00Z'), -300), '2026-12-31')     // year boundary
  assert.equal(G.dayKey(Date.parse('2027-01-01T05:00:00Z'), -300), '2027-01-01')
  assert.equal(G.addDays('2026-12-31', 1), '2027-01-01'); assert.equal(G.addDays('2026-03-01', -1), '2026-02-28')
  assert.deepEqual(G.dayList(Date.parse('2026-11-25T03:00:00Z'), -300), ['2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-27', '2026-11-28', '2026-11-29', '2026-11-30', '2026-12-01'])
  assert.equal(G.dayList(Date.parse('2026-11-25T03:00:00Z'), 0)[1], '2026-11-25')
})

test('filters: day, sport, league, combined, live, My Games', () => {
  const evs = events([stored(1, 'nba', '2026-11-25T23:00:00Z', 'in'), stored(2, 'nhl', '2026-11-25T23:30:00Z'), stored(3, 'nfl', '2026-11-26T18:00:00Z'),
    stored(4, 'ncaab', '2026-11-26T01:00:00Z', 'post'), stored(5, 'wnba', '2026-11-25T20:00:00Z')])
  const f = o => ({ day: '2026-11-25', offset: 0, sport: 'all', league: 'all', mode: 'all', pins: {}, groups: GROUPS, ...o })
  const ids = o => G.select(evs, f(o)).map(e => e.eventId)
  assert.deepEqual(ids({}), ['5', '1', '2'])                                   // chronological on the selected day
  assert.deepEqual(ids({ offset: -300 }), ['5', '1', '2', '4'])                // 01:00 UTC is still the 25th in EST
  assert.deepEqual(ids({ sport: 'basketball' }), ['5', '1'])
  assert.deepEqual(ids({ league: 'nhl' }), ['2'])
  assert.deepEqual(ids({ sport: 'basketball', league: 'nba' }), ['1'])
  assert.deepEqual(ids({ mode: 'live' }), ['1'])
  assert.deepEqual(ids({ mode: 'pinned', pins: { 3: 1, 4: 1 } }), ['4', '3'])  // My Games spans the loaded days
  const counts = G.countsByDay(evs, f({ sport: 'basketball' }), ['2026-11-25', '2026-11-26'])
  assert.deepEqual(counts, { '2026-11-25': 2, '2026-11-26': 1 })
})

test('sections: LIVE, UPCOMING, FINISHED, then postponed/cancelled; chronological inside', () => {
  const evs = events([stored(1, 'nba', '2026-11-25T23:00:00Z', 'post'), stored(2, 'nba', '2026-11-25T21:00:00Z', 'in'), stored(3, 'nba', '2026-11-25T20:00:00Z'),
    stored(4, 'nba', '2026-11-25T19:00:00Z', 'in'), stored(5, 'nba', '2026-11-25T18:00:00Z', 'post', { status: { state: 'post', postponed: true } }),
    stored(6, 'nba', '2026-11-25T17:00:00Z', 'pre', { status: { state: 'pre', cancelled: true } }), stored(7, 'nba', '2026-11-25T16:00:00Z', 'pre', { status: { state: 'pre', delayed: true } })])
  const s = G.sections(evs.slice().sort((a, b) => a.startMs - b.startMs))
  assert.deepEqual(s.map(x => [x.section, x.events.map(e => e.eventId)]), [['live', ['4', '2']], ['upcoming', ['7', '3']], ['finished', ['1']], ['off', ['6', '5']]])
})

test('pins: by event id, survive reload, corrupted storage recovers, prune keeps finished/recent pins', () => {
  const store = { v: null, getItem() { return this.v }, setItem(k, v) { this.v = v } }
  let pins = G.togglePin({}, '401', T0); pins = G.togglePin(pins, '402', T0)
  G.writePins(store, pins)
  assert.deepEqual(Object.keys(G.readPins(store)), ['401', '402'])
  assert.deepEqual(Object.keys(G.togglePin(G.readPins(store), '401', T0)), ['402'])     // unpin
  for (const bad of ['{not json', '[1,2]', '"str"', '{"abc":1,"5":"x","7":3}']) { store.v = bad; assert.deepEqual(G.readPins(store), bad.includes('"7":3') ? { 7: 3 } : {}) }
  assert.deepEqual(G.readPins({ getItem() { throw new Error('denied') } }), {})
  const evs = events([stored(401, 'nba', '2026-11-24T23:00:00Z', 'post')])
  const pruned = G.prunePins({ 401: T0 - 40 * 86400000, 402: T0 - 40 * 86400000, 403: T0 - 2 * 86400000 }, evs, T0)
  assert.deepEqual(Object.keys(pruned).sort(), ['401', '403'])     // finished-but-loaded and recent pins stay; old unknown id goes
})

test('live overlay: matched by event id, stable fields kept, missing score never blanks, other teams ignored', () => {
  const [ev] = events([stored(9, 'nba', '2026-11-25T15:00:00Z', 'in')])
  const live = G.liveFields(espnEvent(9, 'in', 78, 74, { period: 3, clock: '4:21', detail: 'Q3 4:21' }))
  assert.equal(G.merge(ev, live, T0), true)
  assert.deepEqual([ev.score, ev.period, ev.clock, ev.display, ev.home.name], [{ home: 78, away: 74 }, 3, '4:21', 'Q3 4:21', 'Home Team'])
  const noScore = G.liveFields(espnEvent(9, 'in', 'x', '', { period: 3 }))
  G.merge(ev, noScore, T0); assert.deepEqual(ev.score, { home: 78, away: 74 })
  assert.equal(G.merge(ev, G.liveFields(espnEvent(10, 'in', 1, 1)), T0), false)            // other id
  assert.equal(G.merge(ev, G.liveFields(espnEvent(9, 'in', 1, 1, { homeId: '99' })), T0), false)   // other teams for this id
  const done = G.liveFields(espnEvent(9, 'post', 101, 99, { detail: 'Final', name: 'STATUS_FINAL' }))
  G.merge(ev, done, T0); assert.deepEqual([G.section(ev), ev.clock, ev.detail], ['finished', null, 'Final'])
  // home/away only from ESPN flags
  const flipped = espnEvent(11, 'in', 5, 3); flipped.competitions[0].competitors.forEach(c => { c.homeAway = 'home' })
  assert.equal(G.liveFields(flipped), null)
})

test('poll plan: only live / about-to-start leagues, one request per league and ESPN date', () => {
  const evs = events([stored(1, 'nba', '2026-11-25T14:00:00Z', 'in'), stored(2, 'nba', '2026-11-25T14:30:00Z', 'in'),
    stored(3, 'nhl', '2026-11-26T00:00:00Z'), stored(4, 'nfl', '2026-11-25T15:05:00Z'), stored(5, 'mlb', '2026-11-25T14:00:00Z', 'post'),
    stored(6, 'ncaab', '2026-11-25T03:00:00Z', 'pre', { status: { state: 'pre', postponed: true } })])
  const p = G.plan(evs, T0, {})
  assert.deepEqual(p.leagues, { nba: ['20261125'], nfl: ['20261125'] })     // NHL starts in 9 h; MLB is over; postponed never polls
  assert.equal(p.intervalMs, G.POLL.liveMs)
  assert.equal(G.plan(evs, T0, { 1: 1 }).intervalMs, G.POLL.pinnedLiveMs)
  assert.equal(G.plan(events([stored(3, 'nhl', '2026-11-26T00:00:00Z')]), T0, {}).intervalMs, null)
  assert.deepEqual(G.scoreboardUrl('ncaab', '20261125'), 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=20261125&groups=50&limit=500')
})

// ------------------------------------------------------------- Poller with a fake clock
function harness(list, answers) {
  const h = { now: T0, timers: [], calls: [], hidden: false, updates: 0, evs: events(list), pins: {} }
  h.poller = new G.Poller({
    now: () => h.now, fetchJson: url => { h.calls.push(url); const a = answers(url); return a instanceof Error ? Promise.reject(a) : Promise.resolve(a) },
    setTimer: (fn, ms) => { const t = { fn, at: h.now + ms }; h.timers.push(t); return t }, clearTimer: t => { h.timers = h.timers.filter(x => x !== t) },
    hidden: () => h.hidden, events: () => h.evs, pins: () => h.pins, onUpdate: () => { h.updates++ }
  })
  h.advance = async ms => {                  // run due timers in order
    const end = h.now + ms
    for (;;) {
      h.timers.sort((a, b) => a.at - b.at); const t = h.timers[0]
      if (!t || t.at > end) break
      h.timers.shift(); h.now = t.at; await t.fn(); await new Promise(r => setImmediate(r))
    }
    h.now = end
  }
  return h
}
const board = (...evs) => ({ events: evs })

test('poller: no live games -> no requests; NBA live -> only NBA; NHL joins when it starts; stops when finished', async () => {
  let nbaState = 'in', nhlState = 'pre'
  const h = harness([stored(1, 'nba', '2026-11-25T14:00:00Z', 'in'), stored(2, 'nhl', '2026-11-25T16:00:00Z')], url =>
    /nba/.test(url) ? board(espnEvent(1, nbaState, 50, 40, nbaState === 'post' ? { detail: 'Final', name: 'STATUS_FINAL' } : {})) : board(espnEvent(2, nhlState, 1, 0, { detail: 'P1 12:00' })))
  h.poller.start(); await h.advance(0)
  assert.deepEqual(h.calls.map(u => u.split('/sports/')[1].split('/scoreboard')[0]), ['basketball/nba'])
  await h.advance(60000)
  assert.ok(h.calls.length >= 3 && h.calls.every(u => /nba/.test(u)), 'only NBA while NHL is hours away')
  const perMin = h.calls.length
  h.now = Date.parse('2026-11-25T15:52:00Z'); await h.advance(0)            // NHL game 8 min away: starts polling too
  await h.advance(26000)
  assert.ok(h.calls.some(u => /hockey/.test(u)))
  nbaState = 'post'; await h.advance(26000)
  assert.equal(G.section(h.evs[0]), 'finished')
  const before = h.calls.filter(u => /nba/.test(u)).length; await h.advance(120000)
  assert.equal(h.calls.filter(u => /nba/.test(u)).length, before, 'finished league is no longer polled')
  assert.ok(perMin <= 4, `about ${perMin} NBA requests per minute`)
  h.poller.stop()
})

test('poller: never one request per game; hidden tab reduces, visible wakes immediately', async () => {
  const list = Array.from({ length: 40 }, (_, i) => stored(100 + i, 'ncaab', '2026-11-25T14:30:00Z', 'in'))
  const h = harness(list, () => board(...list.map(e => espnEvent(e.eventId, 'in', 10, 8))))
  h.poller.start(); await h.advance(0)
  assert.equal(h.calls.length, 1, '40 live games, one scoreboard request')
  h.hidden = true; const n = h.calls.length; await h.advance(4 * 60000)
  assert.equal(h.calls.length, n, 'no polling for a hidden tab within 5 minutes')
  h.hidden = false; h.poller.wake(); await h.advance(0)
  assert.equal(h.calls.length, n + 1, 'visible again: refreshed at once')
  h.poller.stop()
})

test('poller: a failed live fetch keeps the shown score, marks stale, and recovery updates', async () => {
  let fail = false, score = 50
  const h = harness([stored(1, 'nba', '2026-11-25T14:00:00Z', 'in')], () => fail ? new Error('HTTP 503') : board(espnEvent(1, 'in', score, 40)))
  h.poller.start(); await h.advance(0)
  assert.equal(h.evs[0].score.home, 50)
  fail = true; score = 60; await h.advance(4 * 26000)
  assert.equal(h.evs[0].score.home, 50, 'never blanked')
  const p = G.plan(h.evs, h.now, {})
  assert.deepEqual(h.poller.stale(h.now, p), ['nba'])
  fail = false; await h.advance(26000)
  assert.equal(h.evs[0].score.home, 60); assert.deepEqual(h.poller.stale(h.now, G.plan(h.evs, h.now, {})), [])
  h.poller.stop()
})

test('query parameters: valid values applied, unknown ones fall back', () => {
  const days = G.dayList(T0, 0)
  assert.deepEqual(G.parseQuery('?league=NFL', GROUPS, days), { sport: 'football', league: 'nfl', mode: 'all', day: null })
  assert.deepEqual(G.parseQuery('?league=ncaab&filter=live', GROUPS, days), { sport: 'basketball', league: 'ncaab', mode: 'live', day: null })
  assert.deepEqual(G.parseQuery('?sport=basketball&filter=pinned&date=2026-11-27', GROUPS, days), { sport: 'basketball', league: 'all', mode: 'pinned', day: '2026-11-27' })
  assert.deepEqual(G.parseQuery('?league=XFL&sport=cricket&filter=all&date=2030-01-01', GROUPS, days), { sport: 'all', league: 'all', mode: 'all', day: null })
  assert.deepEqual(G.parseQuery('?date=2026-11-25%3Cscript%3E', GROUPS, days).day, null)
})
