// Unit tests for the grouped sport navigation (nav.js) and the Sports Hub (index.html).
// Run:  node --test tests/test_nav.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { GROUPS } = require('../nav.js')
const read = p => readFileSync(new URL('../' + p, import.meta.url), 'utf8')

test('four sport groups with the agreed leagues; Player Lookup is not a sport', () => {
  assert.deepEqual(GROUPS.map(g => [g.label, g.leagues.map(l => l.label)]), [
    ['American Football', ['NFL', 'NCAAF']],
    ['Basketball', ['NBA', 'WNBA', 'NCAAB']],
    ['Baseball', ['MLB']],
    ['Ice Hockey', ['NHL']],
  ])
  const keys = GROUPS.flatMap(g => g.leagues.map(l => l.key))
  assert.equal(new Set(keys).size, 7)
  assert.ok(!keys.includes('lookup'))
})

test('the Sports Hub shows exactly the nav.js groups, in order, each league a direct link', () => {
  const hub = read('index.html')
  const sections = [...hub.matchAll(/<section class="sport-card [^"]*" data-sport-group="([a-z]+)"[\s\S]*?<h2[^>]*>([^<]+)<\/h2>([\s\S]*?)<\/section>/g)]
  assert.deepEqual(sections.map(m => m[1]), GROUPS.map(g => g.id))
  sections.forEach((m, i) => {
    assert.equal(m[2], GROUPS[i].label)
    const links = [...m[3].matchAll(/<a class="league-link" href="leagues\/([a-z]+)\/"><span class="league-code">([^<]+)<\/span>/g)].map(x => [x[1], x[2]])
    assert.deepEqual(links, GROUPS[i].leagues.map(l => [l.key, l.label]))
  })
  // Player Lookup: one separate card outside the sport grid
  assert.equal((hub.match(/href="lookup\/"/g) || []).length, 1)
  const grid = hub.slice(hub.indexOf('<div class="sport-grid">'), hub.indexOf('<a class="lookup-card"'))
  assert.ok(!grid.includes('lookup/'))
})

test('every league page and Player Lookup use the shared nav with the right root and current page', () => {
  const pages = { 'leagues/nfl/index.html': 'nfl', 'leagues/ncaaf/index.html': 'ncaaf', 'leagues/nba/index.html': 'nba', 'leagues/wnba/index.html': 'wnba',
    'leagues/mlb/index.html': 'mlb', 'leagues/nhl/index.html': 'nhl', 'leagues/ncaab/index.html': 'ncaab', 'lookup/index.html': 'lookup' }
  for (const [p, key] of Object.entries(pages)) {
    const root = p.startsWith('lookup/') ? '../' : '../../'
    const html = read(p)
    const m = html.match(/<nav class="top-nav" aria-label="Roster Monitor" data-nav-root="([^"]+)" data-nav-current="([a-z]+)">[\s\S]*?<\/nav>\s*<script src="([^"]+)"><\/script>/)
    assert.ok(m, p)
    assert.deepEqual([m[1], m[2], m[3]], [root, key, root + 'nav.js'], p)
    assert.equal((html.match(/class="top-nav"/g) || []).length, 1, p)
  }
})

test('nav.js is part of the offline app shell; data/ and logs/ are still never cached', () => {
  const sw = read('service-worker.js')
  assert.match(sw, /'nav\.js',/)
  assert.match(sw, /const LIVE_DATA_FOLDERS = \['data\/', 'logs\/'\]/)
})
