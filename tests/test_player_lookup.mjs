// Unit tests for lookup/player-lookup.js (Player Team Finder matching). Synthetic rosters in the
// same shape as data/<league>/status.json + latest/<TEAM>.json. Run:  node --test tests/test_player_lookup.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
const P = createRequire(import.meta.url)('../lookup/player-lookup.js')

const row = (teamCode, teamName, extra = {}) => ({ teamCode, teamName, checkedAt: '2026-11-20T17:00:00+00:00',
  lastSuccessfulCheckedAt: '2026-11-20T17:00:00+00:00', sourceIssue: '', ...extra })
const roster = (team, players, checkedAt = '2026-11-20T17:00:00+00:00') => ({ team, checkedAt,
  players: players.map(([name, position, number = '', status = 'ACTIVE']) => ({ name, position, number, status })) })

function leagues(overrides = {}) {
  const base = {
    nfl: { status: [row('KC', 'Kansas City Chiefs'), row('ARI', 'Arizona Cardinals'), row('NYG', 'New York Giants')],
           rosters: { KC: roster('Kansas City Chiefs', [['Patrick Mahomes', 'QB', '15', 'ACT'], ['John Smith', 'TE']]),
                      ARI: roster('Arizona Cardinals', [['Marvin Harrison Jr.', 'WR', '18', 'ACT'], ['Ka’imi Fairbairn', 'K']]),
                      NYG: roster('New York Giants', [['Jaxon Smith-Njigba', 'WR'], ['Robert Griffin III', 'QB']]) } },
    ncaaf: { status: [row('ALA', 'Alabama Crimson Tide')],
             rosters: { ALA: roster('Alabama Crimson Tide', [['John Smith', 'LB'], ['Ty Simpson', 'QB']]) } },
    nba: { status: [row('PHX', 'Phoenix Suns')], rosters: { PHX: roster('Phoenix Suns', [['Devin Booker', 'G', '1']]) } },
    wnba: { status: [row('HOU', 'Houston Comets'), row('LV', 'Las Vegas Aces')],
            rosters: { HOU: roster('Houston Comets', [['Brittney Griner', 'C', '42']]),
                       LV: roster('Las Vegas Aces', [["A'ja Wilson", 'C', '22']]),
                       // the old Connecticut file is still on disk but CON is not a monitored team any more
                       CON: roster('Connecticut Sun', [['Brittney Griner', 'C', '42']], '2026-11-09T19:35:03+00:00') } },
    mlb: { status: [row('NYY', 'New York Yankees'), row('ARI', 'Arizona Diamondbacks')],
           rosters: { NYY: roster('New York Yankees', [['Aaron Judge', 'RF', '99', 'A']]),
                      ARI: roster('Arizona Diamondbacks', [['Adrian Del Castillo', 'DH', '25', 'A']]) } },
    nhl: { status: [row('EDM', 'Edmonton Oilers', { sourceIssue: 'fetch_error', lastError: '503' })],
           rosters: { EDM: roster('Edmonton Oilers', [['Connor McDavid', 'C', '97'], ['Ryan Nugent-Hopkins', 'C', '93']], '2026-11-19T17:00:00+00:00') } },
  }
  return P.LEAGUES.map(l => ({ ...l, ...base[l.key], ...(overrides[l.key] || {}) }))
}
const index = (o) => P.buildIndex(leagues(o))
const one = (name, o) => P.lookup(index(o), name)

test('index counts only teams listed in status.json', () => {
  const idx = index()
  assert.deepEqual(idx.counts, { nfl: 6, ncaaf: 2, nba: 1, wnba: 2, mlb: 2, nhl: 2 })
  assert.equal(idx.size, 15)
})

for (const [name, team, league, position] of [
  ['Patrick Mahomes', 'Kansas City Chiefs', 'NFL', 'QB'],
  ['Ty Simpson', 'Alabama Crimson Tide', 'NCAAF', 'QB'],
  ['Devin Booker', 'Phoenix Suns', 'NBA', 'G'],
  ["A'ja Wilson", 'Las Vegas Aces', 'WNBA', 'C'],
  ['Aaron Judge', 'New York Yankees', 'MLB', 'RF'],
  ['Connor McDavid', 'Edmonton Oilers', 'NHL', 'C'],
]) {
  test(`exact ${league} lookup: ${name}`, () => {
    const r = one(name)
    assert.equal(r.status, 'Exact')
    assert.deepEqual([r.match.team, r.match.league, r.match.position], [team, league, position])
  })
}

test('letter case and extra spaces are still an exact match', () => {
  assert.equal(one('PATRICK mahomes').status, 'Exact')
  assert.equal(one('   Patrick    Mahomes  ').status, 'Exact')
  assert.equal(one('patrick\tmahomes').status, 'Exact')
})

test('curly and straight apostrophes match exactly; a missing apostrophe is a normalized match', () => {
  assert.equal(one('A’ja Wilson').status, 'Exact')
  assert.equal(one("Ka'imi Fairbairn").status, 'Exact')
  const r = one('Aja Wilson')
  assert.equal(r.status, 'Normalized'); assert.equal(r.match.team, 'Las Vegas Aces')
})

test('hyphen and space variants of a double-barrelled name are a normalized match', () => {
  assert.equal(one('Jaxon Smith-Njigba').status, 'Exact')
  assert.equal(one('Jaxon Smith–Njigba').status, 'Exact')      // en dash
  const r = one('Jaxon Smith Njigba'); assert.equal(r.status, 'Normalized'); assert.equal(r.match.team, 'New York Giants')
  assert.equal(one('Ryan Nugent Hopkins').match.team, 'Edmonton Oilers')
})

test('Jr / Jr. / ", Jr." formats match; a missing suffix is only a possible match', () => {
  assert.equal(one('Marvin Harrison Jr.').status, 'Exact')
  for (const v of ['Marvin Harrison Jr', 'Marvin Harrison, Jr.', 'marvin harrison jr']) {
    const r = one(v); assert.ok(['Exact', 'Normalized'].includes(r.status), v); assert.equal(r.match.team, 'Arizona Cardinals')
  }
  const bare = one('Marvin Harrison')
  assert.equal(bare.status, 'Not found'); assert.equal(bare.match, null)
  assert.deepEqual(bare.possible.map(p => p.name), ['Marvin Harrison Jr.'])
})

test('Roman numeral suffixes match regardless of case', () => {
  assert.equal(one('Robert Griffin III').status, 'Exact')
  assert.equal(one('robert griffin iii').status, 'Exact')
  assert.equal(one('Robert Griffin II').status, 'Not found')   // a different suffix is a different person
})

test('accents are a normalized match', () => {
  const idx = index({ mlb: { status: [row('NYY', 'New York Yankees')], rosters: { NYY: roster('New York Yankees', [['Ali Sánchez', 'C']]) } } })
  assert.equal(P.lookup(idx, 'Ali Sánchez').status, 'Exact')
  assert.equal(P.lookup(idx, 'Ali Sanchez').status, 'Normalized')
})

test('the same name on two current teams is Ambiguous with every candidate and no chosen team', () => {
  const r = one('John Smith')
  assert.equal(r.status, 'Ambiguous'); assert.equal(r.match, null)
  assert.deepEqual(r.candidates.map(c => `${c.team} (${c.league})`).sort(), ['Alabama Crimson Tide (NCAAF)', 'Kansas City Chiefs (NFL)'])
})

test('names that only differ by punctuation on two teams are also Ambiguous', () => {
  const idx = index({ nba: { status: [row('PHX', 'Phoenix Suns'), row('BOS', 'Boston Celtics')],
    rosters: { PHX: roster('Phoenix Suns', [["D'Andre Hunter", 'F']]), BOS: roster('Boston Celtics', [['DAndre Hunter', 'G']]) } } })
  const r = P.lookup(idx, "D'Andre Hunter")
  assert.equal(r.status, 'Ambiguous'); assert.equal(r.candidates.length, 2)
  assert.ok(r.candidates.find(c => c.team === 'Phoenix Suns').exactSpelling)
})

test('the same name listed twice on one team is still one team', () => {
  const idx = index({ nba: { status: [row('PHX', 'Phoenix Suns')], rosters: { PHX: roster('Phoenix Suns', [['Devin Booker', 'G'], ['Devin Booker', 'G']]) } } })
  assert.equal(P.lookup(idx, 'Devin Booker').status, 'Exact')
})

test('an unknown player is Not found with no team invented', () => {
  const r = one('Nobody Atall')
  assert.equal(r.status, 'Not found'); assert.equal(r.match, null); assert.deepEqual(r.possible, [])
})

test('a small spelling difference is offered only as a possible match', () => {
  const r = one('Patrick Mahomez')
  assert.equal(r.status, 'Not found'); assert.equal(r.match, null)
  assert.deepEqual(r.possible.map(p => p.name), ['Patrick Mahomes'])
})

test('multiple pasted names, blank lines, list markers and duplicates', () => {
  const text = '1. Patrick Mahomes\n\n   \n- Devin Booker\npatrick mahomes\n(3) Connor McDavid\n•  Nobody Atall\n12345\n'
  const results = P.lookupAll(index(), text)
  assert.deepEqual(results.map(r => [r.input, r.status, r.count]), [
    ['Patrick Mahomes', 'Exact', 2], ['Devin Booker', 'Exact', 1], ['Connor McDavid', 'Exact', 1], ['Nobody Atall', 'Not found', 1]])
})

test('WNBA: Houston is the current team; the kept Connecticut file is not indexed', () => {
  const r = one('Brittney Griner')
  assert.equal(r.status, 'Exact'); assert.equal(r.match.team, 'Houston Comets'); assert.equal(r.match.teamCode, 'HOU')
  assert.ok(!JSON.stringify(index().byNormalized.get('brittney griner')).includes('Connecticut'))
})

test('current roster wins: history is never consulted', () => {
  // only status.json teams and their latest rosters are inputs; a moved player shows the new team
  const idx = index({ nba: { status: [row('BOS', 'Boston Celtics')], rosters: { BOS: roster('Boston Celtics', [['Devin Booker', 'G']]) } } })
  assert.equal(P.lookup(idx, 'Devin Booker').match.team, 'Boston Celtics')
})

test('a team with a source issue keeps its last good roster and is flagged', () => {
  const r = one('Connor McDavid')
  assert.equal(r.match.team, 'Edmonton Oilers'); assert.equal(r.match.sourceIssue, 'fetch_error')
  assert.equal(r.match.checkedAt, '2026-11-19T17:00:00+00:00')
})

test('a roster file that could not be loaded is reported, not guessed', () => {
  const idx = index({ nba: { status: [row('PHX', 'Phoenix Suns'), row('BOS', 'Boston Celtics')], rosters: { PHX: roster('Phoenix Suns', [['Devin Booker', 'G']]) } } })
  assert.deepEqual(idx.missingTeams, [{ league: 'NBA', teamCode: 'BOS', teamName: 'Boston Celtics' }])
})

test('PDF text: names are recognised against the index; headers, numbers and other text are ignored', () => {
  const text = ['ROSTER REPORT - WEEK 12', 'Page 1 of 2', 'No. Player Pos Team', '15 Patrick Mahomes QB KC',
    '1  Devin Booker  G  PHX', 'McDavid, Connor', 'Generated 21/11/2026 10:00', '- Totally Unknown Person - -', 'Marvin Harrison Jr. WR'].join('\n')
  const found = P.detectNames(index(), text)
  assert.deepEqual(found.names, ['Patrick Mahomes', 'Devin Booker', 'Connor McDavid', 'Marvin Harrison Jr.'])
  assert.equal(found.matchedLines, 4)
  assert.deepEqual(found.unrecognised, ['Totally Unknown Person'])
})

test('PDF table rows: "Last, First" inside a row is recognised; random "X, Y" text is not', () => {
  const found = P.detectNames(index(), ['97 McDavid, Connor C EDM', 'Booker, Devin G 1', 'Notes, Revised Friday', 'Smith, Jane Doe'].join('\n'))
  assert.deepEqual(found.names, ['Connor McDavid', 'Devin Booker'])
  assert.equal(found.ignoredLines, 2)
})

test('PDF text with no player names finds nothing', () => {
  const found = P.detectNames(index(), 'Quarterly budget review\nRevenue grew 4% year on year\nAppendix A')
  assert.deepEqual(found.names, []); assert.equal(found.matchedLines, 0)
})

test('CSV export has the agreed columns, one row per input, quoted safely', () => {
  const results = P.lookupAll(index(), 'Patrick Mahomes\nJohn Smith\nNobody Atall\nMarvin Harrison')
  const lines = P.toCsv(results).split('\n')
  assert.equal(lines[0], '"input_name","matched_name","team","team_code","league","position","match_status","candidates"')
  assert.equal(lines[1], '"Patrick Mahomes","Patrick Mahomes","Kansas City Chiefs","KC","NFL","QB","Exact",""')
  assert.match(lines[2], /^"John Smith","","","","","","Ambiguous","John Smith - (Kansas City Chiefs \(NFL\)|Alabama Crimson Tide \(NCAAF\)); /)
  assert.equal(lines[3], '"Nobody Atall","","","","","","Not found",""')
  assert.equal(lines[4], '"Marvin Harrison","","","","","","Not found","Possible matches, not confirmed: Marvin Harrison Jr. - Arizona Cardinals (NFL)"')
  assert.equal(lines.length, 5)
  assert.ok(!/\d{4}-\d{2}-\d{2}T|GMT|EST/.test(P.toCsv(results)))   // no timestamps, so no time zone
  const tricky = P.toCsv([{ input: 'Say "Hi", ok', status: 'Not found', match: null, candidates: [], possible: [] }])
  assert.equal(tricky.split('\n')[1], '"Say ""Hi"", ok","","","","","","Not found",""')
})
