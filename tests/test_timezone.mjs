// Unit tests for timezone.js (site-wide GMT / EST display). No browser: the real file runs in a
// small stand-in window. Run from the repository root:  node --test tests/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const SOURCE = readFileSync(new URL('../timezone.js', import.meta.url), 'utf8')

/** Load timezone.js with `stored` in localStorage (undefined = nothing saved, Error = storage broken). */
function load(stored) {
  const data = new Map()
  if (typeof stored === 'string') data.set('roster-monitor-timezone', stored)
  const storage = stored instanceof Error
    ? { getItem() { throw stored }, setItem() { throw stored } }
    : { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)) }
  const window = { localStorage: storage, addEventListener() {} }
  const document = {
    readyState: 'complete', body: null,
    documentElement: { setAttribute() {} },
    querySelectorAll: () => [],
    addEventListener() {},
  }
  class MutationObserver { observe() {} }
  const context = vm.createContext({ window, document, MutationObserver, NodeFilter: {}, Date, Object, String, isNaN })
  vm.runInContext(SOURCE, context)
  return { api: window.RosterTime, data }
}

// the pages' current helper, verbatim (leagues/*/index.html): GMT output must not change
const pageGmt = value => (!value ? '-' : new Date(value).toLocaleString('en-GB', { timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' GMT')

const SAMPLES = ['2026-11-20T17:37:35.815298+00:00', '2026-08-01T10:42:08.904902+00:00', '2026-11-22T02:00:00Z',
                 '2026-01-01T00:00:00Z', '2026-07-04T23:59:59.999Z', '2026-11-09T19:35:03.536610+00:00']

test('fresh browser defaults to GMT', () => {
  assert.equal(load(undefined).api.get(), 'GMT')
})

test('invalid or unusable stored values fall back to GMT', () => {
  for (const bad of ['PST', 'est', 'EDT', '', 'null', '{"zone":"EST"}']) assert.equal(load(bad).api.get(), 'GMT', bad)
  assert.equal(load(new Error('storage disabled')).api.get(), 'GMT')
})

test('a saved EST choice is used', () => {
  assert.equal(load('EST').api.get(), 'EST')
})

test('GMT output is exactly what the pages print today', () => {
  const { api } = load(undefined)
  for (const s of SAMPLES) assert.equal(api.format(s, 'GMT'), pageGmt(s), s)
  assert.equal(api.format('', 'GMT'), pageGmt(''))
})

test('EST is a fixed UTC-5, including in summer (no EDT)', () => {
  const { api } = load(undefined)
  assert.equal(api.format('2026-11-20T17:37:35.815Z', 'EST'), '20/11/2026, 12:37:35 EST')
  assert.equal(api.format('2026-07-01T12:00:00Z', 'EST'), '01/07/2026, 07:00:00 EST')  // EDT would say 08:00
  assert.equal(api.format('2026-03-08T12:00:00Z', 'EST'), '08/03/2026, 07:00:00 EST')  // US DST start day
})

test('EST crosses midnight, month end, year end and leap day correctly', () => {
  const { api } = load(undefined)
  assert.equal(api.format('2026-11-22T02:00:00Z', 'EST'), '21/11/2026, 21:00:00 EST')
  assert.equal(api.format('2026-12-01T04:59:59Z', 'EST'), '30/11/2026, 23:59:59 EST')
  assert.equal(api.format('2027-01-01T03:00:00Z', 'EST'), '31/12/2026, 22:00:00 EST')
  assert.equal(api.format('2028-03-01T01:00:00Z', 'EST'), '29/02/2028, 20:00:00 EST')
  assert.equal(api.format('2026-11-22T05:00:00Z', 'EST'), '22/11/2026, 00:00:00 EST')  // exactly midnight
})

test('displayed text converts GMT -> EST and back to the identical original', () => {
  const { api } = load(undefined)
  for (const s of SAMPLES) {
    const shown = `Last update: ${pageGmt(s)}`
    const est = api.convertText(shown, 'EST')
    assert.equal(est, `Last update: ${api.format(s, 'EST')}`)
    assert.equal(api.convertText(est, 'GMT'), shown)
  }
})

test('several stamps in one text are all converted; other text is left alone', () => {
  const { api } = load(undefined)
  const text = 'Checked: 22/11/2026, 02:00:00 GMT (last attempt 22/11/2026, 04:30:00 GMT) | MEDIUM'
  assert.equal(api.convertText(text, 'EST'),
    'Checked: 21/11/2026, 21:00:00 EST (last attempt 21/11/2026, 23:30:00 EST) | MEDIUM')
  for (const plain of ['Players: 14', 'Last movement: 2 days ago', 'Times shown in GMT.', '12/11/2026 GMT', 'Player #22'])
    assert.equal(api.convertText(plain, 'EST'), plain)
})

test('text already in the chosen zone is not changed (no double conversion)', () => {
  const { api } = load(undefined)
  const est = '21/11/2026, 21:00:00 EST'
  assert.equal(api.convertText(est, 'EST'), est)
  assert.equal(api.convertText(api.convertText(est, 'EST'), 'EST'), est)
})

test('choosing a zone saves it; an invalid choice saves GMT', () => {
  const { api, data } = load(undefined)
  api.set('EST')
  assert.equal(api.get(), 'EST')
  assert.equal(data.get('roster-monitor-timezone'), 'EST')
  api.set('PST')
  assert.equal(api.get(), 'GMT')
  assert.equal(data.get('roster-monitor-timezone'), 'GMT')
})

test('a broken storage does not stop switching', () => {
  const { api } = load(new Error('quota'))
  api.set('EST')
  assert.equal(api.get(), 'EST')
})
