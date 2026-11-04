/*
 * Client-side projection math. Runs in the browser (loaded by map.html as a
 * plain <script>, no build step) and under Node's built-in test runner (see
 * tests/test_estimate.js) - so no ES module syntax, no dependencies.
 *
 * Two ways an area's still-uncounted vote gets projected:
 *
 *   1. Historical-swing model, when a historical baseline exists for the
 *      area: compare the area's own counted-so-far D/R split to its
 *      historical (prior-cycle) split, get a swing, dampen that swing by how
 *      much of the state has actually reported (both by this area's own vote
 *      share and by how much of the STATE's expected vote the areas
 *      reporting so far actually represent - see confidenceWeight and
 *      expectedTotalVotes), and apply the dampened swing to the historical
 *      split for the remaining vote.
 *
 *   2. Flat fallback, when no historical baseline exists for the area (no
 *      seat-year match, non-county geography, redistricting-affected
 *      district - see races.py). Assumes the remaining vote splits exactly
 *      like the vote counted so far: Predicted = Real * 100 / PercentIn.
 *      This is the formula fetch_results.py used to compute server-side for
 *      every area; it now lives here as the honest floor when we have
 *      nothing better to go on.
 *
 * At 0% counted, neither model applies - there is nothing to extrapolate
 * from, so estimateArea returns predictions of null rather than guessing.
 *
 * A THIRD CANDIDATE (an independent, or whoever else polls best besides the
 * Democrat and Republican) is a separate bloc, `indReal`. Both models above are
 * Democrat-versus-Republican models: the baseline is last cycle's two-party
 * split, and the swing is the counted two-party share against it. Once a third
 * candidate is a real contender (independentIsMaterial) that split no longer
 * describes the race, so all three blocs are projected flat: each candidate's
 * counted votes x 100 / percentIn. Below that threshold the third candidate is
 * ignored entirely and every number is exactly what it was before they existed.
 * leaderOf and callOf are three-way versions of the rules map.html always had,
 * and are identical to them when there is no third candidate.
 */

// Full trust in the observed swing once this fraction of the state's votes
// are in - short of that, the swing is dampened proportionally.
const VOTE_TRUST_THRESHOLD = 0.5;
// Full trust once areas covering this fraction of the state's total expected
// vote (reporting + not-yet-reporting) have reported - guards against one
// large, unrepresentative county alone driving percentIn up. This is a
// fraction of actual vote weight, not a raw area count, because a single
// big-city county reporting alone can be more representative than five tiny
// rural counties - see expectedTotalVotes for how "the state's total
// expected vote" is known even for areas that haven't reported at all.
const AREA_VOTE_TRUST_THRESHOLD = 0.3;

// How far a state's expected total ballots may be moved from its baseline
// by the turnout observed so far, as a multiple. A guard against one freak
// early county, on top of the trust ramp below.
const TURNOUT_SCALE_MIN = 0.5;
const TURNOUT_SCALE_MAX = 2;

// A third candidate counts as a real contender once they hold this share of
// the votes counted so far in the race (statewide, or the district), and at
// least this many votes. Below that they are ignored: their votes stay inside
// NBC's all-candidate total only, as they always did. The vote floor stops a
// handful of early ballots from switching a race between two models.
const INDEPENDENT_MIN_SHARE = 0.05;
const INDEPENDENT_MIN_VOTES = 1000;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

// totalVotes is NBC's all-candidate count, so the share is the same 43.7%
// that NBC prints, not a share of the three blocs.
function independentIsMaterial(independentVotes, totalVotes) {
  return independentVotes >= INDEPENDENT_MIN_VOTES
    && totalVotes > 0
    && independentVotes / totalVotes >= INDEPENDENT_MIN_SHARE;
}

// Who is ahead. Exactly the old two-way rule unless a third candidate has
// votes; a tie for first (including between the other two) is "tie".
function leaderOf(dem, rep, ind = 0) {
  if (!(ind > 0)) {
    if (dem > rep) return "D";
    if (rep > dem) return "R";
    return "tie";
  }
  const top = Math.max(dem, rep, ind);
  const leaders = [dem === top && "D", rep === top && "R", ind === top && "I"].filter(Boolean);
  return leaders.length === 1 ? leaders[0] : "tie";
}

// How far the best of three is ahead of the second best.
function leadOver(a, b, c) {
  const [top, second] = [a, b, c].sort((x, y) => y - x);
  return top - second;
}

// Three-way call, not a boolean "certain": how much of the race is settled.
//   "certain":     decided no matter what is still to come.
//   "projected":   not yet decided, but our own projection has one candidate
//                  clear of the field.
//   "unprojected": too little to call.
// With two candidates the test is the one map.html always used - a side's
// votes against half of the expected ballots - kept verbatim. With a third, a
// majority of the ballots is too strict (a 45/40/15 race may never reach it),
// so the test becomes a plurality lock: the leader's lead over the runner-up
// must exceed every vote that could still go to the runner-up. For two
// candidates the two are the same inequality (lead > T/2 <=> lead - second >
// T - lead - second), which the tests check over thousands of random cases.
//   certain   - counted lead     > expected ballots - ballots counted
//   projected - projected lead   > expected ballots - ballots we projected
// (the second term is the silent counties, which nobody's projection covers).
function callOf(demReal, repReal, demPredicted, repPredicted, ballotEstimate, indReal = 0, indPredicted = 0) {
  if (!(indReal > 0) && !(indPredicted > 0)) {
    const half = ballotEstimate / 2;
    if (Math.max(demReal, repReal) > half) return "certain";
    if (Math.max(demPredicted, repPredicted) > half) return "projected";
    return "unprojected";
  }
  const stillToCount = Math.max(ballotEstimate - (demReal + repReal + indReal), 0);
  if (leadOver(demReal, repReal, indReal) > stillToCount) return "certain";
  const notProjected = Math.max(ballotEstimate - (demPredicted + repPredicted + indPredicted), 0);
  if (leadOver(demPredicted, repPredicted, indPredicted) > notProjected) return "projected";
  return "unprojected";
}

// historicalEntry is whatever historical_<race>.json has for this area's
// FIPS/GEOID, or undefined/null if the area has none - see
// build_historical_baseline.py for which areas are absent and why (no
// seat-year match, non-county geography, redistricting drift).
function historicalShare(historicalEntry) {
  if (!historicalEntry) return null;
  const { demShare, repShare, year } = historicalEntry;
  if (!Number.isFinite(demShare) || !Number.isFinite(repShare)) return null;
  return { demShare, repShare, year };
}

// Vote-weighted Dem share of a state, from its counties' reference entries
// ([fips, entry] pairs, e.g. historical_president.json). Null if none.
function stateShareFromCounties(countyEntries) {
  let dem = 0, total = 0;
  for (const [, entry] of countyEntries) {
    if (!historicalShare(entry) || !(entry.votes > 0)) continue;
    dem += entry.demShare * entry.votes;
    total += entry.votes;
  }
  return total > 0 ? dem / total : null;
}

// A county baseline for a race that only has a statewide result (Senate):
// the county's own lean in a reference race (President), shifted by how much
// the statewide result ran ahead of or behind that reference race. Without
// this, every county would be compared against the state average, so a
// normally deep-blue or deep-red county would read as a huge "swing".
function shiftedCountyBaseline(countyReference, stateEntry, stateReferenceShare) {
  const county = historicalShare(countyReference);
  const state = historicalShare(stateEntry);
  if (!county || !state || stateReferenceShare == null) return null;
  const demShare = clamp(county.demShare + (state.demShare - stateReferenceShare), 0, 1);
  return { demShare, repShare: 1 - demShare, year: state.year };
}

// Deviation of the counted-so-far Dem share from the historical Dem share,
// in the same [-1, 1] units as demShare itself. Null if nothing is counted
// yet (nothing to compare) or there's no historical share to compare against.
function observedSwing(demReal, repReal, historical) {
  if (!historical) return null;
  const counted = demReal + repReal;
  if (!(counted > 0)) return null;
  return demReal / counted - historical.demShare;
}

// 0 (trust nothing yet, use the historical share as-is) to 1 (trust the
// observed swing fully) - the product of two independent checks so that
// neither this area's own percentIn nor a lopsided sample of the state's
// counties alone can force full confidence.
//
// reportingVoteFraction: fraction (0-1) of the state's total expected vote
// (reporting + not-yet-reporting, using historical totals for the latter -
// see expectedTotalVotes) accounted for by areas currently reporting. The
// caller computes this once per state, not per area - see map.html.
function confidenceWeight(percentIn, reportingVoteFraction) {
  return confidenceParts(percentIn, reportingVoteFraction).weight;
}

// The two factors of confidenceWeight, kept separate so the UI can show them.
function confidenceParts(percentIn, reportingVoteFraction) {
  const voteComponent = clamp((percentIn / 100) / VOTE_TRUST_THRESHOLD, 0, 1);
  const areaComponent = clamp(reportingVoteFraction / AREA_VOTE_TRUST_THRESHOLD, 0, 1);
  return { voteComponent, areaComponent, weight: voteComponent * areaComponent };
}

// An area's own best-guess eventual vote total: self-extrapolated from its
// own percentIn if it has reported anything (all-party totalVotes, unlike
// flatPredict's two-party total), otherwise the historical baseline's own
// past total as a stand-in for an area that hasn't reported at all yet. Null if neither is
// available (no live data AND no historical coverage) - genuinely unknown,
// not a number to guess at.
function expectedTotalVotes(area, historicalEntry) {
  if (area && area.percentIn > 0 && area.totalVotes) {
    return area.totalVotes * 100 / area.percentIn;
  }
  if (historicalEntry && Number.isFinite(historicalEntry.votes)) {
    return historicalEntry.votes;
  }
  return null;
}

// The Dem/Rep split to assume for the vote that hasn't been counted yet.
function projectedRemainingShare(historical, swing, weight) {
  const dem = clamp(historical.demShare + weight * swing, 0, 1);
  return { demShare: dem, repShare: 1 - dem };
}

// Splits remainingVotes (already known non-negative) by remainingShare and
// adds it to what's actually been counted.
function predictFromRemainingShare(demReal, repReal, remainingVotes, remainingShare) {
  return {
    demPredicted: demReal + remainingVotes * remainingShare.demShare,
    repPredicted: repReal + remainingVotes * remainingShare.repShare,
  };
}

// Today's flat extrapolation, kept as the fallback for areas with no
// historical baseline - identical to fetch_results.py's former
// `round(v * 100 / percent_in)`, computed here instead now that the math has
// moved client-side.
function flatPredict(demReal, repReal, percentIn) {
  const scale = 100 / percentIn;
  return { demPredicted: demReal * scale, repPredicted: repReal * scale };
}

/**
 * Project one area's full-count result.
 *
 * area: { demReal, repReal, percentIn, totalVotes } - no per-area
 *   totalExpected: NBC only publishes that at the state level, so an area's
 *   own remaining vote is derived from its own percentIn and D+R counted instead
 *   (identical to how the flat formula always worked).
 * historicalEntry: this area's entry from historical_<race>.json, or
 *   null/undefined if it has none.
 * reportingVoteFraction: fraction (0-1) of the state's total expected vote
 *   accounted for by areas currently reporting - the caller computes this
 *   once per state (see map.html), estimateArea doesn't compute it itself.
 *
 * Returns null predictions (noData: true) at 0% counted; the count itself
 * at 100%; a flat-fallback prediction (usedFallback: true) with no
 * historical entry; otherwise the full swing-blended prediction. Every
 * intermediate value is returned too, so the UI can show the exact working
 * rather than re-deriving it.
 */
function estimateArea(area, historicalEntry, reportingVoteFraction) {
  const { demReal, repReal, percentIn, totalVotes } = area;
  // Only a contender: the caller zeroes a third candidate who is below the
  // threshold. The extra keys below appear ONLY in a race that has one, so every
  // result for a two-candidate race is the object it always was. The race is
  // what matters, not this area's own votes: a county where the third candidate
  // happens to have none is still projected by the same flat model as its
  // neighbours, never by the two-party swing model beside them.
  const contender = area.independentRace === true || area.indReal > 0;
  const indReal = area.indReal > 0 ? area.indReal : 0;

  if (!(percentIn > 0) || !totalVotes) {
    return { demPredicted: null, repPredicted: null, noData: true, usedFallback: false };
  }

  if (percentIn >= 100) {
    return { demPredicted: demReal, repPredicted: repReal, noData: false, usedFallback: false,
             ...(contender ? { indPredicted: indReal } : {}) };
  }

  // A third candidate means last cycle's Democrat-versus-Republican baseline
  // does not describe this race: project all three blocs flat.
  const historical = contender ? null : historicalShare(historicalEntry);
  if (!historical) {
    const { demPredicted, repPredicted } = flatPredict(demReal, repReal, percentIn);
    const scale = 100 / percentIn;
    return { demPredicted, repPredicted, noData: false, usedFallback: true, scale,
             ...(contender ? { indPredicted: indReal * scale, fallbackReason: "independent" } : {}) };
  }

  const counted = demReal + repReal;
  const swing = observedSwing(demReal, repReal, historical);
  const { voteComponent, areaComponent, weight } = confidenceParts(percentIn, reportingVoteFraction);
  const remainingShare = projectedRemainingShare(historical, swing, weight);
  const remainingVotes = counted * (100 - percentIn) / percentIn;
  const { demPredicted, repPredicted } =
    predictFromRemainingShare(demReal, repReal, remainingVotes, remainingShare);

  return {
    demPredicted, repPredicted, noData: false, usedFallback: false,
    historical, swing, weight, remainingShare,
    countedDemShare: counted > 0 ? demReal / counted : null,
    voteComponent, areaComponent, reportingVoteFraction, remainingVotes,
  };
}

// ---- Expected total ballots for a whole state ----
//
// A county that has not reported yet is sized by what it cast last time. That
// ignores how this cycle's turnout is running: if the counties already in are
// projecting 20% above their own last-cycle totals, the rest probably will too
// (and in a midterm, the reverse). The ratio estimator below scales the
// unreported part by that observed ratio, but only as far as it can be trusted.

// Compares the counties that have reported with what the same counties cast
// last time. `areas` is the reporting areas ({fips, demPredicted, repPredicted});
// `stateEntries` is [fips, {votes}] for EVERY county of the state in the
// baseline. Only reporting counties that have a baseline entry count, on both
// sides, so the ratio compares like with like. Null when there is nothing to
// compare.
function turnoutBasis(areas, stateEntries) {
  const votesByFips = new Map();
  let stateHistorical = 0;
  for (const [fips, entry] of stateEntries) {
    if (!entry || !(entry.votes > 0)) continue;
    votesByFips.set(fips, entry.votes);
    stateHistorical += entry.votes;
  }
  let projected = 0, historical = 0;
  for (const area of areas) {
    const votes = votesByFips.get(area.fips);
    // The third candidate's votes are ballots too: last cycle's total was every
    // ballot in the race, so leaving them out would make a race with a strong
    // independent look like a collapse in turnout.
    const total = area.demPredicted + area.repPredicted + (area.indPredicted || 0);
    if (!votes || !Number.isFinite(total)) continue;
    projected += total;
    historical += votes;
  }
  if (!(historical > 0) || !(stateHistorical > 0)) return null;
  return { projected, historical, stateHistorical, ratio: projected / historical };
}

// 0 (ignore the observed turnout) to 1 (use it fully): the share of the
// state's last-cycle vote that the reporting counties hold, against the same
// 30% bar the swing model uses. One small county in proves nothing.
function turnoutTrust(basis) {
  if (!basis) return 0;
  return clamp(basis.historical / basis.stateHistorical / AREA_VOTE_TRUST_THRESHOLD, 0, 1);
}

// County-reported races: the multiple to apply to the unreported counties'
// last-cycle votes. 1 means "no adjustment".
function turnoutScale(basis) {
  const trust = turnoutTrust(basis);
  if (!basis || !(trust > 0)) return 1;
  return clamp(1 + trust * (basis.ratio - 1), TURNOUT_SCALE_MIN, TURNOUT_SCALE_MAX);
}

// State-keyed races (Senate/Governor) have only one baseline total for the
// whole state, so the weights are another race's county votes (President).
// What the state would reach if the rest of it matches the counties already
// in is projected / share; blend that with the baseline total by trust, and
// keep the result within the same multiple of the baseline as above.
function stateBallotEstimate(projected, baselineTotal, basis) {
  const trust = turnoutTrust(basis);
  if (!basis || !(trust > 0) || !(baselineTotal > 0)) return baselineTotal;
  const share = basis.historical / basis.stateHistorical;
  const byTurnout = projected / share;
  const blended = (1 - trust) * baselineTotal + trust * byTurnout;
  return clamp(blended, baselineTotal * TURNOUT_SCALE_MIN, baselineTotal * TURNOUT_SCALE_MAX);
}

const api = {
  INDEPENDENT_MIN_SHARE,
  INDEPENDENT_MIN_VOTES,
  independentIsMaterial,
  leaderOf,
  callOf,
  TURNOUT_SCALE_MIN,
  TURNOUT_SCALE_MAX,
  turnoutBasis,
  turnoutTrust,
  turnoutScale,
  stateBallotEstimate,
  VOTE_TRUST_THRESHOLD,
  AREA_VOTE_TRUST_THRESHOLD,
  observedSwing,
  confidenceWeight,
  confidenceParts,
  expectedTotalVotes,
  projectedRemainingShare,
  flatPredict,
  estimateArea,
  stateShareFromCounties,
  shiftedCountyBaseline,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
} else {
  window.estimate = api;
}
