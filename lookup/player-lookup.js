/* Roster Monitor - Player Lookup: name normalisation, the current-player index and matching.
 *
 * Pure functions, no network and no DOM: the page (lookup/index.html) loads the roster files and
 * passes them in; tests run this file directly. Works as a browser script (window.PlayerLookup) and
 * as a CommonJS module.
 *
 * Source of truth: each league's data/<league>/status.json (the teams currently monitored) and the
 * latest/<TEAM>.json roster of every team listed there - the same files the dashboards show. Teams
 * not in status.json (e.g. a relocated franchise's old code) are never indexed, and history/logs are
 * never used to decide a current team.
 *
 * Matching is conservative:
 *   "Exact"      - same name ignoring letter case, extra spaces and apostrophe / dash styles
 *   "Normalized" - same name also ignoring accents, full stops, apostrophes, hyphen-vs-space and
 *                  suffix punctuation (Jr. = Jr, "Smith, Jr." = "Smith Jr")
 *   "Ambiguous"  - more than one current team has that (normalized) name: all candidates are shown,
 *                  none is chosen
 *   "Not found"  - no current roster has it; close names may be listed as "possible matches", never
 *                  as a result (a missing or extra suffix, or a 1-2 letter difference in the last name)
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory()
  else root.PlayerLookup = factory()
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict'

  var LEAGUES = [
    { key: 'nfl', label: 'NFL' }, { key: 'ncaaf', label: 'NCAAF' }, { key: 'nba', label: 'NBA' },
    { key: 'wnba', label: 'WNBA' }, { key: 'mlb', label: 'MLB' }, { key: 'nhl', label: 'NHL' }
  ]
  var SUFFIXES = { jr: 1, sr: 1, ii: 1, iii: 1, iv: 1, v: 1 }
  // words that mark a heading or table header, not a person ("No. Player Pos Team", "Page Total")
  var HEADER_WORDS = { no: 1, player: 1, players: 1, name: 1, names: 1, pos: 1, position: 1, team: 1, teams: 1,
    roster: 1, page: 1, total: 1, totals: 1, report: 1, date: 1, league: 1, status: 1, number: 1, week: 1,
    season: 1, list: 1, injury: 1, injuries: 1, depth: 1, chart: 1, summary: 1, notes: 1 }

  /** Level 1 ("Exact"): letter case, surrounding/repeated spaces, apostrophe and dash styles. */
  function exactKey(name) {
    return String(name == null ? '' : name).normalize('NFC')
      .replace(/[‘’‛ʼ`´′]/g, "'")
      .replace(/[‐-―−]/g, '-')
      .replace(/\s+/g, ' ').trim().toLowerCase()
  }

  /** Level 2 ("Normalized"): also accents, full stops, apostrophes, hyphen-vs-space, suffix commas. */
  function normalizedKey(name) {
    return exactKey(name).normalize('NFD').replace(/[̀-ͯ]/g, '')
      .replace(/[.']/g, '').replace(/[-,]/g, ' ').replace(/\s+/g, ' ').trim()
  }

  /** Normalized name without a trailing generational suffix (only for "possible matches"). */
  function baseKey(name) {
    var words = normalizedKey(name).split(' ')
    if (words.length > 2 && SUFFIXES[words[words.length - 1]]) words.pop()
    return words.join(' ')
  }

  function lastName(key) {
    var words = key.split(' ')
    if (words.length > 2 && SUFFIXES[words[words.length - 1]]) words.pop()
    return words[words.length - 1]
  }

  /** Line-start bullets / list numbers that are never part of a name ("1. ", "- ", "(3) "). */
  function cleanInputLine(line) {
    return String(line).replace(/^\s*(?:[-*•·▪]|\d{1,3}[.)]|\(\d{1,3}\))\s+/, '').trim()
  }

  /** The input text as names: one per line; blank lines dropped; duplicates folded (count kept). */
  function parseInput(text) {
    var out = [], seen = {}
    String(text || '').split(/\r\n|\r|\n/).forEach(function (raw) {
      var name = cleanInputLine(raw)
      if (!name || !/[a-z]/i.test(name)) return
      var key = normalizedKey(name)
      if (seen[key]) { seen[key].count++; return }
      seen[key] = { input: name, count: 1 }
      out.push(seen[key])
    })
    return out
  }

  /**
   * Build the in-memory index.
   * leagues: [{ key, label, status: [statusRow...], rosters: { TEAMCODE: latestRoster } , summary }]
   * Only teams listed in status.json are indexed; a team whose roster file could not be loaded is
   * reported in `missingTeams` (its players are not invented).
   */
  function buildIndex(leagues) {
    var byExact = new Map(), byNormalized = new Map(), byBase = new Map(), byLast = new Map()
    var counts = {}, missingTeams = []
    function add(map, key, entry) { var list = map.get(key); if (!list) map.set(key, list = []); list.push(entry) }
    leagues.forEach(function (league) {
      counts[league.key] = 0
      ;(league.status || []).forEach(function (row) {
        var roster = league.rosters && league.rosters[row.teamCode]
        if (!roster || !Array.isArray(roster.players)) { missingTeams.push({ league: league.label, teamCode: row.teamCode, teamName: row.teamName }); return }
        roster.players.forEach(function (player) {
          var name = String(player.name || '').trim()
          if (!name) return
          var entry = {
            name: name, league: league.label, leagueKey: league.key,
            team: row.teamName || roster.team || row.teamCode, teamCode: row.teamCode,
            position: player.position || '', number: player.number || '', rosterStatus: player.status || '',
            checkedAt: roster.checkedAt || row.lastSuccessfulCheckedAt || row.checkedAt || '',
            sourceIssue: row.sourceIssue || ''
          }
          var nk = normalizedKey(name)
          add(byExact, exactKey(name), entry); add(byNormalized, nk, entry)
          add(byBase, baseKey(name), entry); add(byLast, lastName(nk), entry)
          counts[league.key]++
        })
      })
    })
    return { byExact: byExact, byNormalized: byNormalized, byBase: byBase, byLast: byLast, counts: counts,
             missingTeams: missingTeams, size: Object.keys(counts).reduce(function (a, k) { return a + counts[k] }, 0) }
  }

  /** One candidate per (league, team): the same name twice on one team is still one team. */
  function distinctTeams(entries) {
    var seen = {}, out = []
    entries.forEach(function (e) { var k = e.leagueKey + '|' + e.teamCode; if (!seen[k]) { seen[k] = 1; out.push(e) } })
    return out
  }

  function levenshtein(a, b) {
    if (a === b) return 0
    var prev = [], cur = [], i, j
    for (j = 0; j <= b.length; j++) prev[j] = j
    for (i = 1; i <= a.length; i++) {
      cur = [i]
      for (j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
      prev = cur
    }
    return prev[b.length]
  }

  /** Close names for a not-found input (never a result): suffix differences, small spelling gaps. */
  function possibleMatches(index, input, limit) {
    var nk = normalizedKey(input), found = new Map()
    ;(index.byBase.get(baseKey(input)) || []).forEach(function (e) { found.set(e.leagueKey + '|' + e.teamCode + '|' + e.name, e) })
    var last = lastName(nk), first = nk.split(' ')[0]
    var pool = []
    // same last name (first name differs a little), or same first name with a nearby last name
    ;(index.byLast.get(last) || []).forEach(function (e) { pool.push(e) })
    if (pool.length < 2000) {
      index.byLast.forEach(function (list, key) {
        if (key !== last && Math.abs(key.length - last.length) <= 2 && key[0] === last[0] && levenshtein(key, last) <= (last.length >= 6 ? 2 : 1)) {
          list.forEach(function (e) { if (normalizedKey(e.name).split(' ')[0] === first) pool.push(e) })
        }
      })
    }
    pool.forEach(function (e) {
      var k = normalizedKey(e.name)
      if (k !== nk && levenshtein(k, nk) <= Math.max(1, Math.min(3, Math.floor(nk.length * 0.2)))) found.set(e.leagueKey + '|' + e.teamCode + '|' + e.name, e)
    })
    return Array.from(found.values()).slice(0, limit || 5)
  }

  /** Match one name. Returns { input, status, match, candidates, possible }. */
  function lookup(index, input) {
    var exact = distinctTeams(index.byExact.get(exactKey(input)) || [])
    var normalized = distinctTeams(index.byNormalized.get(normalizedKey(input)) || [])
    if (normalized.length > 1) {
      return { input: input, status: 'Ambiguous', match: null, possible: [],
               candidates: normalized.map(function (e) { return Object.assign({ exactSpelling: exact.indexOf(e) >= 0 || exactKey(e.name) === exactKey(input) }, e) }) }
    }
    if (normalized.length === 1) {
      var e = normalized[0]
      return { input: input, status: exactKey(e.name) === exactKey(input) ? 'Exact' : 'Normalized', match: e, candidates: [], possible: [] }
    }
    return { input: input, status: 'Not found', match: null, candidates: [], possible: possibleMatches(index, input, 5) }
  }

  function lookupAll(index, text) {
    return parseInput(text).map(function (item) { var r = lookup(index, item.input); r.count = item.count; return r })
  }

  /**
   * Names in extracted PDF text, recognised against the index (the index tells names from headers,
   * numbers and other text). Each line is scanned for runs of 2-5 words that are a current player's
   * name; "Last, First" lines are also tried as "First Last". Returns
   * { names: [original text...], matchedLines, ignoredLines, unrecognised: [name-like lines] }.
   */
  function detectNames(index, text) {
    var names = [], seen = {}, matchedLines = 0, ignoredLines = 0, unrecognised = []
    function keep(original) {
      var key = normalizedKey(original)
      if (seen[key]) return
      seen[key] = 1; names.push(original.replace(/\s+/g, ' ').trim())
    }
    String(text || '').split(/\r\n|\r|\n/).forEach(function (raw) {
      var line = raw.replace(/\s+/g, ' ').trim()
      if (!line) return
      var hit = false
      // "Last, First" anywhere in the line (e.g. a table row "97 McDavid, Connor C EDM"), kept only
      // when the swapped name is a current player; one- and two-word forms are both tried
      var W = "[A-Z][A-Za-zÀ-ɏ'’.-]+", comma = new RegExp('(' + W + '(?: ' + W + ')?), (' + W + ')(?: (' + W + '))?', 'g'), m
      while ((m = comma.exec(line))) {
        var lasts = m[1].indexOf(' ') > 0 ? [m[1], m[1].split(' ').pop()] : [m[1]]
        var firsts = m[3] ? [m[2] + ' ' + m[3], m[2]] : [m[2]]
        search: for (var a = 0; a < lasts.length; a++) for (var f = 0; f < firsts.length; f++) {
          var swapped = firsts[f] + ' ' + lasts[a]
          if (index.byNormalized.has(normalizedKey(swapped))) { keep(swapped); hit = true; break search }
        }
      }
      if (!hit) {
        var words = line.split(' ')
        for (var i = 0; i < words.length; i++) {
          for (var n = Math.min(5, words.length - i); n >= 2; n--) {
            var chunk = words.slice(i, i + n).join(' ').replace(/^[^\wÀ-ɏ]+|[^\w.À-ɏ]+$/g, '')
            if (chunk && index.byNormalized.has(normalizedKey(chunk))) { keep(chunk); hit = true; i += n - 1; break }
          }
        }
      }
      if (hit) matchedLines++
      else {
        ignoredLines++
        // looks like a person's name (2-4 capitalised words, no digits) but is on no current roster
        // table cells without letters ("15", "-", "|") at either end are not part of the name
        var core = line.replace(/^(?:[^A-Za-zÀ-ɏ]+ )+|(?: [^A-Za-zÀ-ɏ]+)+$/g, '')
        if (/^[A-Z][A-Za-zÀ-ɏ'’.-]+(?: [A-Z][A-Za-zÀ-ɏ'’.-]+){1,3}$/.test(core) && unrecognised.length < 200 &&
            !normalizedKey(core).split(' ').some(function (w) { return HEADER_WORDS[w] })) unrecognised.push(core)
      }
    })
    return { names: names, matchedLines: matchedLines, ignoredLines: ignoredLines, unrecognised: unrecognised }
  }

  function csvCell(value) { return '"' + String(value == null ? '' : value).replace(/"/g, '""') + '"' }

  /** Results as CSV (no timestamps, so it does not depend on the display time zone). */
  function toCsv(results) {
    var rows = [['input_name', 'matched_name', 'team', 'team_code', 'league', 'position', 'match_status', 'candidates']]
    results.forEach(function (r) {
      var m = r.match || {}
      var others = r.status === 'Ambiguous' ? r.candidates : r.status === 'Not found' ? r.possible : []
      var note = others.map(function (c) { return c.name + ' - ' + c.team + ' (' + c.league + ')' }).join('; ')
      if (note && r.status === 'Not found') note = 'Possible matches, not confirmed: ' + note
      rows.push([r.input, m.name || '', m.team || '', m.teamCode || '', m.league || '', m.position || '', r.status, note])
    })
    return rows.map(function (row) { return row.map(csvCell).join(',') }).join('\n')
  }

  return { LEAGUES: LEAGUES, exactKey: exactKey, normalizedKey: normalizedKey, baseKey: baseKey,
           parseInput: parseInput, buildIndex: buildIndex, lookup: lookup, lookupAll: lookupAll,
           detectNames: detectNames, toCsv: toCsv }
})
