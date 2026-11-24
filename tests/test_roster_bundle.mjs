// Unit tests for roster-bundle.js (data/<league>/rosters.json loader with per-team fallback).
// Run:  node --test tests/test_roster_bundle.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
const B = createRequire(import.meta.url)('../roster-bundle.js')

const RUN = '2026-11-24T07:49:18.122840+00:00'
const status = [
  { teamCode: 'DUKE', teamName: 'Duke Blue Devils', checkedAt: '2026-11-24T07:46:00+00:00', sourceIssue: '' },
  { teamCode: 'UL', teamName: "Louisiana Ragin' Cajuns", checkedAt: '', sourceIssue: 'fetch_error' },
]
const summary = { lastRunFinished: RUN }
const players = [{ name: 'A One', position: 'G', number: '1', status: 'ACTIVE', class: 'Senior', providerPlayerId: '1' }]
const bundle = () => ({ league: 'ncaab', generatedAt: RUN, teamCount: 2, playerCount: 1, teams: [
  { teamCode: 'DUKE', teamName: 'Duke Blue Devils', teamId: '150', conference: 'ACC', logo: '', checkedAt: '2026-11-24T07:46:00+00:00',
    source: 'ESPN', rosterSource: 'site', sourceIssue: '', players },
  { teamCode: 'UL', teamName: "Louisiana Ragin' Cajuns", teamId: '309', conference: 'Sun Belt', logo: '', checkedAt: '',
    source: '', rosterSource: '', sourceIssue: 'fetch_error', players: null }] })

test('a bundle consistent with summary.json and status.json is used', () => {
  const r = B.check(bundle(), status, summary)
  assert.equal(r.ok, true)
  assert.deepEqual(Object.keys(r.rosters), ['DUKE'])                // a team with no saved roster is not invented
  assert.deepEqual(r.rosters.DUKE.players, players)
  assert.equal(r.rosters.DUKE.rosterSource, 'site')
})

test('a bundle from another run, with other teams or other check times is rejected', () => {
  const cases = {
    'different run': b => { b.generatedAt = '2026-11-23T07:00:00+00:00' },
    'missing team': b => { b.teams.pop() },
    'extra team': b => { b.teams.push({ ...b.teams[0], teamCode: 'X' }) },
    'renamed team': b => { b.teams[1].teamCode = 'ULL' },
    'stale team check time': b => { b.teams[0].checkedAt = '2026-11-23T07:46:00+00:00' },
    'duplicate team': b => { b.teams[1] = { ...b.teams[0] } },
    'players not a list': b => { b.teams[0].players = 'x' },
    'no teams list': b => { delete b.teams },
  }
  for (const [name, mutate] of Object.entries(cases)) {
    const b = bundle(); mutate(b)
    assert.equal(B.check(b, status, summary).ok, false, name)
  }
  assert.equal(B.check(null, status, summary).ok, false)
  assert.equal(B.check(bundle(), status, {}).ok, false)
})

function fakeFetch(files) {
  const calls = []
  return { calls, fetchJson: async path => { calls.push(path); if (!(path in files)) throw new Error('404 ' + path); return structuredClone(files[path]) } }
}
const latestDuke = { code: 'DUKE', team: 'Duke Blue Devils', checkedAt: '2026-11-24T07:46:00+00:00', players }

test('load uses the bundle with a single request when it is valid', async () => {
  const f = fakeFetch({ 'base/data/ncaab/rosters.json': bundle() })
  const r = await B.load({ base: 'base/', league: 'ncaab', statusRows: status, summary, fetchJson: f.fetchJson })
  assert.equal(r.via, 'bundle'); assert.equal(r.requests, 1); assert.deepEqual(f.calls, ['base/data/ncaab/rosters.json'])
})

test('load falls back to every per-team file when the bundle is stale or missing', async () => {
  const stale = bundle(); stale.generatedAt = 'old'
  for (const files of [{ 'base/data/ncaab/rosters.json': stale }, {}]) {
    files['base/data/ncaab/latest/DUKE.json'] = latestDuke
    const f = fakeFetch(files)
    const r = await B.load({ base: 'base/', league: 'ncaab', statusRows: status, summary, fetchJson: f.fetchJson, concurrency: 2 })
    assert.equal(r.via, 'per-team'); assert.equal(r.requests, 3)
    assert.deepEqual(r.rosters.DUKE.players, players)                    // the authoritative per-team file wins
    assert.deepEqual(r.rosters.UL, { players: [], errors: ['404 base/data/ncaab/latest/UL.json'] })
    assert.ok(r.reason)
  }
})

test('a broken bundle can never override the per-team rosters', async () => {
  const broken = bundle(); broken.teams[0].players = []; broken.teams[0].checkedAt = 'wrong'
  const f = fakeFetch({ 'base/data/ncaab/rosters.json': broken, 'base/data/ncaab/latest/DUKE.json': latestDuke })
  const r = await B.load({ base: 'base/', league: 'ncaab', statusRows: status, summary, fetchJson: f.fetchJson })
  assert.equal(r.via, 'per-team'); assert.deepEqual(r.rosters.DUKE.players, players)
})
