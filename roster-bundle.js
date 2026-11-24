/* Roster Monitor - league roster bundle loader (used by the NCAAB dashboard and Player Lookup).
 *
 * data/<league>/rosters.json is a convenience copy of the league's latest/<TEAM>.json files, written
 * at the end of each checker run. latest/ stays the source of truth: the bundle is used only when it
 * is consistent with the same run's summary.json and status.json, otherwise every team's latest file
 * is loaded instead. A missing, stale or broken bundle therefore never hides or overrides a roster.
 *
 * check(bundle, statusRows, summary) -> { ok, reason, rosters }
 *   ok only if: bundle.generatedAt === summary.lastRunFinished, the bundle has exactly the teams in
 *   status.json, and each team's checkedAt equals its status row's checkedAt.
 * load({ base, league, statusRows, summary, fetchJson, concurrency, onProgress })
 *   -> Promise<{ rosters, via: 'bundle' | 'per-team', reason, requests }>
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory()
  else root.RosterBundle = factory()
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict'

  function check(bundle, statusRows, summary) {
    var fail = function (reason) { return { ok: false, reason: reason, rosters: null } }
    if (!bundle || typeof bundle !== 'object' || !Array.isArray(bundle.teams)) return fail('bundle missing or not readable')
    if (!summary || !summary.lastRunFinished || bundle.generatedAt !== summary.lastRunFinished)
      return fail('bundle is from a different run than summary.json')
    var rows = Array.isArray(statusRows) ? statusRows : []
    if (bundle.teams.length !== rows.length) return fail('bundle has ' + bundle.teams.length + ' teams, status.json has ' + rows.length)
    var byCode = {}
    for (var i = 0; i < bundle.teams.length; i++) {
      var t = bundle.teams[i]
      if (!t || typeof t.teamCode !== 'string' || byCode[t.teamCode]) return fail('bundle team list is malformed')
      byCode[t.teamCode] = t
    }
    var rosters = {}
    for (var j = 0; j < rows.length; j++) {
      var row = rows[j], team = byCode[row.teamCode]
      if (!team) return fail('team ' + row.teamCode + ' is missing from the bundle')
      if ((team.checkedAt || '') !== (row.checkedAt || '')) return fail('team ' + row.teamCode + ' differs from status.json')
      if (team.players != null && !Array.isArray(team.players)) return fail('team ' + row.teamCode + ' has unreadable players')
      if (team.players != null) {
        rosters[row.teamCode] = { code: team.teamCode, team: team.teamName, teamId: team.teamId, conference: team.conference,
                                  logo: team.logo, checkedAt: team.checkedAt, source: team.source, rosterSource: team.rosterSource, players: team.players }
      }
    }
    return { ok: true, reason: '', rosters: rosters }
  }

  async function perTeam(opts) {
    var rosters = {}, rows = opts.statusRows || [], next = 0, done = 0, requests = 0
    async function worker() {
      while (next < rows.length) {
        var row = rows[next++]
        requests++
        try { rosters[row.teamCode] = await opts.fetchJson(opts.base + 'data/' + opts.league + '/latest/' + row.teamCode + '.json') }
        catch (e) { rosters[row.teamCode] = { players: [], errors: [e.message] } }
        done++
        if (opts.onProgress && (done % 25 === 0 || done === rows.length)) opts.onProgress(done, rows.length)
      }
    }
    var workers = []
    for (var i = 0; i < (opts.concurrency || 8); i++) workers.push(worker())
    await Promise.all(workers)
    return { rosters: rosters, requests: requests }
  }

  async function load(opts) {
    var bundle = null, reason = ''
    try { bundle = await opts.fetchJson(opts.base + 'data/' + opts.league + '/rosters.json') }
    catch (e) { reason = 'bundle not loaded (' + e.message + ')' }
    if (bundle) {
      var result = check(bundle, opts.statusRows, opts.summary)
      if (result.ok) return { rosters: result.rosters, via: 'bundle', reason: '', requests: 1 }
      reason = result.reason
    }
    var fallback = await perTeam(opts)
    return { rosters: fallback.rosters, via: 'per-team', reason: reason, requests: 1 + fallback.requests }
  }

  return { check: check, load: load }
})
