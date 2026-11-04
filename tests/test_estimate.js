// Pure-math unit tests for estimate.js. Run with `node --test tests/test_estimate.js`.
// No browser, no Selenium - see tests/README.md for why the rest of this
// suite needs a real browser and this file doesn't.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  confidenceWeight,
  observedSwing,
  projectedRemainingShare,
  expectedTotalVotes,
  flatPredict,
  estimateArea,
  stateShareFromCounties,
  shiftedCountyBaseline,
  confidenceParts,
  turnoutBasis,
  turnoutTrust,
  turnoutScale,
  stateBallotEstimate,
  TURNOUT_SCALE_MIN,
  TURNOUT_SCALE_MAX,
  INDEPENDENT_MIN_SHARE,
  INDEPENDENT_MIN_VOTES,
  independentIsMaterial,
  leaderOf,
  callOf,
} = require("../estimate.js");

test("confidenceWeight is 0 with nothing counted", () => {
  assert.equal(confidenceWeight(0, 0), 0);
});

test("confidenceWeight reaches full trust at 50% in with enough of the state's vote accounted for", () => {
  assert.equal(confidenceWeight(50, 0.3), 1);
  assert.equal(confidenceWeight(100, 1), 1); // never exceeds 1
});

test("confidenceWeight dampens proportionally below the vote threshold", () => {
  assert.equal(confidenceWeight(10, 0.3), 0.2); // 10/50
});

test("confidenceWeight dampens an unrepresentative sample of the state's vote (hole #5)", () => {
  // 30% of THIS area's own vote is in either way, but in one case the
  // counties reporting so far only account for 5% of the STATE's total
  // expected vote (a small, possibly unrepresentative sample); in the other
  // they account for 30% (a much more substantial slice).
  const smallSample = confidenceWeight(30, 0.05);
  const largeSample = confidenceWeight(30, 0.3);
  assert.ok(smallSample < largeSample);
  assert.equal(largeSample, 0.6); // 30/50 * 1 (vote fraction already at threshold)
  assert.ok(smallSample < 0.6);
});

test("observedSwing is null with nothing counted", () => {
  assert.equal(observedSwing(0, 0, { demShare: 0.5, repShare: 0.5 }), null);
});

test("observedSwing is null with no historical baseline", () => {
  assert.equal(observedSwing(10, 5, null), null);
});

test("observedSwing matches history exactly when the counted split equals it", () => {
  const historical = { demShare: 0.2, repShare: 0.8 };
  assert.equal(observedSwing(20, 80, historical), 0);
});

test("projectedRemainingShare clamps to [0,1]", () => {
  const historical = { demShare: 0.9, repShare: 0.1 };
  const share = projectedRemainingShare(historical, 0.5, 1); // 0.9 + 0.5 = 1.4, clamp to 1
  assert.equal(share.demShare, 1);
  assert.equal(share.repShare, 0);
});

test("expectedTotalVotes self-extrapolates from an area's own percentIn when it has reported", () => {
  const area = { percentIn: 25, totalVotes: 500 };
  assert.equal(expectedTotalVotes(area, { votes: 999999 }), 2000); // 500 * 100/25, ignores historical
});

test("expectedTotalVotes falls back to the historical total when nothing has reported yet", () => {
  assert.equal(expectedTotalVotes(null, { votes: 42000 }), 42000);
  assert.equal(expectedTotalVotes({ percentIn: 0, totalVotes: 0 }, { votes: 42000 }), 42000);
});

test("expectedTotalVotes is null with neither live data nor a historical baseline", () => {
  assert.equal(expectedTotalVotes(null, null), null);
  assert.equal(expectedTotalVotes({ percentIn: 0, totalVotes: 0 }, null), null);
});

test("flatPredict matches the original server-side formula exactly", () => {
  // Was `round(v * 100 / percent_in)` in fetch_results.py before the math
  // moved client-side; no rounding here since intermediate math shouldn't
  // round early, but the ratio must be identical.
  const { demPredicted, repPredicted } = flatPredict(300, 200, 25);
  assert.equal(demPredicted, 1200); // 300 * 100/25
  assert.equal(repPredicted, 800);
});

test("estimateArea returns no prediction at 0% counted, even with a historical baseline", () => {
  const area = { demReal: 0, repReal: 0, percentIn: 0, totalVotes: 0 };
  const result = estimateArea(area, { demShare: 0.51, repShare: 0.49 }, 0);
  assert.equal(result.noData, true);
  assert.equal(result.demPredicted, null);
  assert.equal(result.repPredicted, null);
});

test("estimateArea returns the exact real counts at 100% counted", () => {
  const area = { demReal: 5100, repReal: 4900, percentIn: 100, totalVotes: 10000 };
  const result = estimateArea(area, { demShare: 0.2, repShare: 0.8 }, 1);
  assert.equal(result.demPredicted, 5100);
  assert.equal(result.repPredicted, 4900);
});

test("estimateArea falls back to the flat formula with no historical baseline", () => {
  const area = { demReal: 300, repReal: 200, percentIn: 25, totalVotes: 500 };
  const result = estimateArea(area, null, 0.3);
  assert.equal(result.usedFallback, true);
  assert.equal(result.demPredicted, 1200);
  assert.equal(result.repPredicted, 800);
});

test("estimateArea's swing path projects the same two-party total as flatPredict", () => {
  const area = { demReal: 450, repReal: 450, percentIn: 50, totalVotes: 1000 };
  const result = estimateArea(area, { demShare: 0.5, repShare: 0.5 }, 1);
  const flat = flatPredict(450, 450, 50);
  assert.equal(result.demPredicted + result.repPredicted, 1800);
  assert.equal(flat.demPredicted + flat.repPredicted, 1800);
});

// Worked scenarios: swing agreeing vs. contradicting history, at 10% and
// 50% counted, mirroring the design discussion. 1,000,000 expected votes,
// historical baseline D 51% / R 49%, and the state's reporting counties
// assumed to already account for the full expected vote (reportingVoteFraction
// of 1) so these isolate the vote-share (percentIn) component of confidence.
function scenario(percentInPct, demShareCounted, reportingVoteFraction) {
  const totalExpected = 1_000_000;
  const totalVotes = Math.round(totalExpected * (percentInPct / 100));
  const demReal = Math.round(totalVotes * demShareCounted);
  const repReal = totalVotes - demReal;
  const area = { demReal, repReal, percentIn: percentInPct, totalVotes };
  return estimateArea(area, { demShare: 0.51, repShare: 0.49 }, reportingVoteFraction);
}

test("swing agreeing with history: 10% in nudges the projection only slightly", () => {
  const result = scenario(10, 0.56, 1); // counted split D56/R44, S=+0.05
  // weight = confidenceWeight(10,1) = 0.2 -> dampened swing = 0.01
  // remaining share = 0.51 + 0.01 = 0.52
  const total = result.demPredicted + result.repPredicted;
  assert.ok(Math.abs(result.demPredicted / total - 0.524) < 0.001);
});

test("swing agreeing with history: 50% in fully trusts the observed split", () => {
  const result = scenario(50, 0.55, 1); // weight = 1 -> remaining share = counted share
  const total = result.demPredicted + result.repPredicted;
  assert.ok(Math.abs(result.demPredicted / total - 0.55) < 0.001);
});

test("swing contradicting history: 10% in barely moves the projection, not to the counted split", () => {
  const result = scenario(10, 0.35, 1); // counted split D35/R65, S=-0.16
  // weight = 0.2 -> dampened swing = -0.032 -> remaining share = 0.478
  const total = result.demPredicted + result.repPredicted;
  const demFraction = result.demPredicted / total;
  assert.ok(demFraction > 0.35, "should not fully believe a small early sample");
  assert.ok(demFraction < 0.51, "should still move away from pure history");
  assert.ok(Math.abs(demFraction - 0.465) < 0.001);
});

test("swing contradicting history: 50% in, sustained, flips the projected leader", () => {
  const result = scenario(50, 0.38, 1); // weight = 1 -> fully trusted, statewide leader flips
  assert.ok(result.demPredicted < result.repPredicted);
  const total = result.demPredicted + result.repPredicted;
  assert.ok(Math.abs(result.demPredicted / total - 0.38) < 0.001);
});

test("stateShareFromCounties is the vote-weighted Dem share, skipping unusable entries", () => {
  const share = stateShareFromCounties([
    ["1", { demShare: 0.8, repShare: 0.2, votes: 300 }],
    ["2", { demShare: 0.2, repShare: 0.8, votes: 100 }],
    ["3", { votes: 1000 }], // votes-only entry: no share to weigh
  ]);
  assert.ok(Math.abs(share - 0.65) < 1e-9);
  assert.equal(stateShareFromCounties([]), null);
});

test("shiftedCountyBaseline keeps the county's own lean, moved by the state's gap to the reference race", () => {
  // State Senate ran 2pt more D than the state's presidential result.
  const b = shiftedCountyBaseline({ demShare: 0.73, repShare: 0.27 }, { demShare: 0.51, repShare: 0.49, year: 2020 }, 0.49);
  assert.ok(Math.abs(b.demShare - 0.75) < 1e-9);
  assert.ok(Math.abs(b.repShare - 0.25) < 1e-9);
  assert.equal(b.year, 2020);
  assert.equal(shiftedCountyBaseline({ demShare: 0.99, repShare: 0.01 }, { demShare: 0.6, repShare: 0.4 }, 0.4).demShare, 1);
  assert.equal(shiftedCountyBaseline(undefined, { demShare: 0.5, repShare: 0.5 }, 0.5), null);
  assert.equal(shiftedCountyBaseline({ demShare: 0.5, repShare: 0.5 }, { votes: 100 }, 0.5), null); // votes-only state
});

test("a deep-blue county counting at its usual level is not dragged to the state mean", () => {
  // Fulton-like: normally ~73% D, 20% counted at 73% D, in a ~49% D state.
  const area = { demReal: 73, repReal: 27, totalVotes: 100, percentIn: 20 };
  const stateBaseline = { demShare: 0.491, repShare: 0.509 };
  const countyBaseline = shiftedCountyBaseline({ demShare: 0.727, repShare: 0.273 }, stateBaseline, 0.489);
  const dragged = estimateArea(area, stateBaseline, 1);
  const own = estimateArea(area, countyBaseline, 1);
  assert.ok(dragged.remainingShare.demShare < 0.6); // the old bug: ~58.7% D for the rest
  assert.ok(Math.abs(own.remainingShare.demShare - 0.73) < 0.01);
});

test("confidenceParts' two factors multiply to confidenceWeight", () => {
  const parts = confidenceParts(20, 0.15);
  assert.equal(parts.voteComponent, 0.4);  // 20% in / 50%
  assert.equal(parts.areaComponent, 0.5);  // 15% of the state's vote / 30%
  assert.equal(parts.weight, 0.2);
  assert.equal(confidenceWeight(20, 0.15), parts.weight);
});

test("estimateArea's returned intermediates reproduce its own projection exactly", () => {
  // The "?" breakdown shows these values as the working, so they must add up.
  const area = { demReal: 3100, repReal: 2900, totalVotes: 6200, percentIn: 30 };
  const r = estimateArea(area, { demShare: 0.46, repShare: 0.54 }, 0.2);
  const counted = area.demReal + area.repReal;
  assert.ok(Math.abs(r.countedDemShare - 3100 / 6000) < 1e-12);
  assert.ok(Math.abs(r.swing - (r.countedDemShare - 0.46)) < 1e-12);
  assert.ok(Math.abs(r.weight - r.voteComponent * r.areaComponent) < 1e-12);
  assert.ok(Math.abs(r.remainingShare.demShare - (0.46 + r.weight * r.swing)) < 1e-12);
  assert.ok(Math.abs(r.remainingVotes - counted * 70 / 30) < 1e-9);
  assert.ok(Math.abs(r.demPredicted - (3100 + r.remainingVotes * r.remainingShare.demShare)) < 1e-9);
  assert.ok(Math.abs(r.repPredicted - (2900 + r.remainingVotes * r.remainingShare.repShare)) < 1e-9);
  assert.equal(r.reportingVoteFraction, 0.2);
});

test("the flat fallback reports the scale it applied", () => {
  const r = estimateArea({ demReal: 300, repReal: 200, totalVotes: 520, percentIn: 25 }, null, 1);
  assert.equal(r.scale, 4);
  assert.equal(r.demPredicted, 300 * r.scale);
  assert.equal(r.repPredicted, 200 * r.scale);
});

// ---- expected total ballots: turnout scaling ----

// The worked example: a 4-county state, every county cast 100k last time; 3
// have reported and each projects to 120k this time.
const FOUR_COUNTIES = ["1", "2", "3", "4"].map(f => [f, { votes: 100000 }]);
const reporting = (fips, total) => fips.map(f => ({ fips: f, demPredicted: total / 2, repPredicted: total / 2 }));

test("turnoutBasis compares reporting counties with the same counties' last-cycle votes", () => {
  const basis = turnoutBasis(reporting(["1", "2", "3"], 120000), FOUR_COUNTIES);
  assert.deepEqual(basis, { projected: 360000, historical: 300000, stateHistorical: 400000, ratio: 1.2 });
});

test("worked example: 3 of 4 counties at 120k vs 100k scale the silent county's 100k to 120k (460k -> 480k)", () => {
  const basis = turnoutBasis(reporting(["1", "2", "3"], 120000), FOUR_COUNTIES);
  assert.equal(turnoutTrust(basis), 1);       // they hold 75% of last time's vote, well past 30%
  assert.ok(Math.abs(turnoutScale(basis) - 1.2) < 1e-12);
  assert.ok(Math.abs(360000 + 100000 * turnoutScale(basis) - 480000) < 1e-6);
});

test("the state-keyed form lands on the same 480k, not the flat 400k historical total", () => {
  const basis = turnoutBasis(reporting(["1", "2", "3"], 120000), FOUR_COUNTIES);
  assert.ok(Math.abs(stateBallotEstimate(360000, 400000, basis) - 480000) < 1e-6);
});

test("a reporting county with no baseline entry is left out of BOTH sides of the ratio", () => {
  // Including its projection but not its history would inflate the ratio.
  const areas = [...reporting(["1", "2"], 100000), ...reporting(["99"], 500000)];
  const basis = turnoutBasis(areas, FOUR_COUNTIES);
  assert.equal(basis.projected, 200000);
  assert.equal(basis.historical, 200000);
  assert.equal(basis.ratio, 1);
});

test("turnout counted below the baseline shrinks the estimate (a midterm against a presidential year)", () => {
  const basis = turnoutBasis(reporting(["1", "2", "3"], 70000), FOUR_COUNTIES);
  assert.ok(Math.abs(turnoutScale(basis) - 0.7) < 1e-12);
  assert.ok(Math.abs(stateBallotEstimate(210000, 400000, basis) - 280000) < 1e-6);
});

test("trust ramps up to full at 30% of last cycle's vote, and below that blends toward 'no adjustment'", () => {
  // One county of four holds 25% of last time's vote: trust 0.25 / 0.3.
  const basis = turnoutBasis(reporting(["1"], 200000), FOUR_COUNTIES);
  const trust = turnoutTrust(basis);
  assert.ok(Math.abs(trust - 0.25 / 0.3) < 1e-12);
  assert.ok(Math.abs(turnoutScale(basis) - (1 + trust * 1)) < 1e-12); // ratio is 2.0
  // One tiny county of a big state proves almost nothing.
  const tiny = turnoutBasis(reporting(["1"], 200000),
    [["1", { votes: 1000 }], ["2", { votes: 999000 }]]);
  assert.ok(turnoutTrust(tiny) < 0.01);
});

test("trust 0 (nothing comparable) means exactly today's numbers", () => {
  assert.equal(turnoutScale(null), 1);
  assert.equal(stateBallotEstimate(360000, 400000, null), 400000);
  assert.equal(turnoutBasis([], FOUR_COUNTIES), null);
  assert.equal(turnoutBasis(reporting(["1"], 1), []), null);
  assert.equal(turnoutBasis(reporting(["99"], 1), FOUR_COUNTIES), null);
});

test("the scale is clamped, so one freak early county cannot move the estimate more than 2x either way", () => {
  const huge = turnoutBasis(reporting(["1", "2", "3"], 10000000), FOUR_COUNTIES);
  assert.equal(turnoutScale(huge), TURNOUT_SCALE_MAX);
  assert.equal(stateBallotEstimate(30000000, 400000, huge), 400000 * TURNOUT_SCALE_MAX);
  const tiny = turnoutBasis(reporting(["1", "2", "3"], 1000), FOUR_COUNTIES);
  assert.equal(turnoutScale(tiny), TURNOUT_SCALE_MIN);
  assert.equal(stateBallotEstimate(3000, 400000, tiny), 400000 * TURNOUT_SCALE_MIN);
});

test("non-finite and non-positive inputs never produce NaN", () => {
  const nan = turnoutBasis([{ fips: "1", demPredicted: NaN, repPredicted: 5 }], FOUR_COUNTIES);
  assert.equal(nan, null);
  assert.equal(stateBallotEstimate(360000, 0, null), 0);
  const basis = turnoutBasis(reporting(["1", "2", "3"], 120000), FOUR_COUNTIES);
  assert.equal(stateBallotEstimate(360000, 0, basis), 0);
});

// ---- a third candidate (independent / other) ----

// The rules map.html had before a third candidate existed, copied verbatim from
// the commit before this change. They are the oracle: with no third candidate the
// new functions must agree with them on EVERY input, not just the obvious ones.
function oldLeaderOf(dem, rep) {
  if (dem > rep) return "D";
  if (rep > dem) return "R";
  return "tie";
}
function oldCallOf(demReal, repReal, demPredicted, repPredicted, ballotEstimate) {
  const half = ballotEstimate / 2;
  if (Math.max(demReal, repReal) > half) return "certain";
  if (Math.max(demPredicted, repPredicted) > half) return "projected";
  return "unprojected";
}

// Small, deterministic generator so a failure reproduces (mulberry32).
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

test("leaderOf is the old two-way rule for any input when there is no third candidate", () => {
  const rand = rng(1);
  for (let i = 0; i < 20000; i++) {
    // Small integers make exact ties common, which is where rules disagree.
    const d = Math.floor(rand() * 6), r = Math.floor(rand() * 6);
    assert.equal(leaderOf(d, r), oldLeaderOf(d, r), `${d} ${r}`);
    assert.equal(leaderOf(d, r, 0), oldLeaderOf(d, r));
    assert.equal(leaderOf(d, r, undefined), oldLeaderOf(d, r));
    assert.equal(leaderOf(d, r, NaN), oldLeaderOf(d, r));
  }
});

test("callOf is the old rule for any input when there is no third candidate", () => {
  const rand = rng(2);
  for (let i = 0; i < 50000; i++) {
    const n = () => Math.floor(rand() * 40);
    const [dr, rr, dp, rp] = [n(), n(), n(), n()];
    // Includes ballotEstimate BELOW the projected total (the old rule never
    // guarded against it) and exact-half ties.
    const T = Math.floor(rand() * 90);
    assert.equal(callOf(dr, rr, dp, rp, T), oldCallOf(dr, rr, dp, rp, T), `${dr} ${rr} ${dp} ${rp} ${T}`);
    assert.equal(callOf(dr, rr, dp, rp, T, 0, 0), oldCallOf(dr, rr, dp, rp, T));
  }
});

test("the plurality lock is the old majority rule whenever there are only two candidates", () => {
  // The claim behind the three-way rule: leader - second > T - (leader + second)
  // is the same inequality as leader > T / 2. Integers keep the arithmetic exact.
  const rand = rng(3);
  const plurality = (a, b, cnt, T) => (Math.abs(a - b) > Math.max(T - (a + b), 0) ? true : false) && cnt;
  for (let i = 0; i < 50000; i++) {
    const a = Math.floor(rand() * 50), b = Math.floor(rand() * 50);
    const T = a + b + Math.floor(rand() * 50);                  // the expected total can't be below what is counted
    const lockedOld = Math.max(a, b) > T / 2;
    const lockedNew = Math.abs(a - b) > T - (a + b);
    assert.equal(lockedNew, lockedOld, `${a} ${b} ${T}`);
  }
});

test("independentIsMaterial needs both 5% of the count and 1,000 votes", () => {
  assert.equal(INDEPENDENT_MIN_SHARE, 0.05);
  assert.equal(INDEPENDENT_MIN_VOTES, 1000);
  assert.equal(independentIsMaterial(50000, 1000000), true);       // exactly 5% counts
  assert.equal(independentIsMaterial(49999, 1000000), false);      // just under
  assert.equal(independentIsMaterial(1000, 20000), true);          // exactly 1,000 votes, exactly 5%
  assert.equal(independentIsMaterial(999, 10000), false);          // under the vote floor even at 9.99%
  assert.equal(independentIsMaterial(999, 1000), false);           // 99.9% of a tiny count is still tiny
  assert.equal(independentIsMaterial(0, 1000000), false);
  assert.equal(independentIsMaterial(5000, 0), false);             // nothing counted: no NaN, no division by zero
  assert.equal(independentIsMaterial(undefined, 100), false);
  assert.equal(independentIsMaterial(NaN, 100), false);
});

test("leaderOf with a third candidate: three-way leader, and a tie for first is a tie", () => {
  assert.equal(leaderOf(30, 40, 50), "I");
  assert.equal(leaderOf(30, 50, 40), "R");
  assert.equal(leaderOf(50, 30, 40), "D");
  assert.equal(leaderOf(50, 50, 10), "tie");     // D and R level at the top
  assert.equal(leaderOf(10, 50, 50), "tie");     // R and I level at the top
  assert.equal(leaderOf(40, 10, 40), "tie");
  assert.equal(leaderOf(10, 10, 50), "I");       // a tie for LAST changes nothing
  assert.equal(leaderOf(0, 40, 30), "R");        // the Democrat did not run
  assert.equal(leaderOf(0, 0, 5), "I");
});

test("a three-way race can be called without anyone reaching 50%", () => {
  // 45 / 40 / 15 of 1,000 expected ballots, all counted: the old rule would say
  // "unprojected" forever; the leader is plainly the winner.
  assert.equal(oldCallOf(450, 400, 450, 400, 1000), "unprojected");
  assert.equal(callOf(450, 400, 450, 400, 1000, 150, 150), "certain");
  // With 200 ballots still out a 50-vote lead can still flip, so it is not locked.
  assert.equal(callOf(450, 400, 450, 400, 1200, 150, 150), "unprojected");
});

test("plurality lock: certain only when the lead beats every vote still to count", () => {
  // Counted D 450, R 400, I 150 = 1000; expected 1100 => 100 still out. Lead over the runner-up is 50.
  assert.equal(callOf(450, 400, 450, 400, 1100, 150, 150), "unprojected");   // 50 < 100 out, and the projection is no clearer
  // 120 out instead of 100 does not help the leader; 30 out does.
  assert.equal(callOf(450, 400, 450, 400, 1030, 150, 150), "certain");       // lead 50 > 30 out
  // A lead of exactly the votes still out is NOT locked (the runner-up could tie).
  assert.equal(callOf(450, 400, 450, 400, 1050, 150, 150), "unprojected");   // 50 = 50 out
});

test("projected: the projected lead must exceed the ballots nobody's projection covers", () => {
  // Counted votes alone do not lock it (lead 50 < 400 still to count) ...
  // ... but each side's projection (D 600, R 300, I 100) leads by 300 and only 50 ballots
  // (silent counties) are unprojected: projected, not certain.
  assert.equal(callOf(300, 250, 600, 300, 1050, 100, 100), "projected");
  // The same projection with 400 ballots in counties nobody projected is not yet a call.
  assert.equal(callOf(300, 250, 600, 300, 1400, 100, 100), "unprojected");
});

test("a three-way tie for first is never called", () => {
  assert.equal(callOf(100, 100, 100, 100, 300, 100, 100), "unprojected");
  assert.equal(callOf(100, 100, 150, 150, 450, 90, 150), "unprojected");     // projected D = R at the top
});

test("the Democrat absent (Nebraska-like): Republican against an independent", () => {
  // Counted R 360, I 313 (673) at ~87% in, so ~774 expected ballots and 101 still to come.
  // The 47-vote lead is not locked ...
  // ... but the projection (R 414, I 360 = 774) covers every expected ballot and R leads it by 54: projected.
  assert.equal(callOf(0, 360, 0, 414, 774, 313, 360), "projected");
  // Fully counted, R ahead: certain.
  assert.equal(callOf(0, 400, 0, 400, 713, 313, 313), "certain");
  // The independent ahead in the projection is called for the independent's side the same way.
  assert.equal(callOf(0, 300, 0, 330, 774, 373, 444), "projected");
  assert.equal(leaderOf(0, 330, 444), "I");
});

test("estimateArea with a third candidate projects all three blocs flat and ignores the baseline", () => {
  const area = { demReal: 100, repReal: 300, indReal: 200, percentIn: 50, totalVotes: 650 };
  const baseline = { demShare: 0.45, repShare: 0.55, votes: 2000, year: 2020 };
  const out = estimateArea(area, baseline, 1);
  assert.equal(out.demPredicted, 200);
  assert.equal(out.repPredicted, 600);
  assert.equal(out.indPredicted, 400);
  assert.equal(out.usedFallback, true);
  assert.equal(out.fallbackReason, "independent");
  assert.equal(out.scale, 2);
  // The three projected blocs add up to what the whole count would be at 100%.
  assert.equal(out.demPredicted + out.repPredicted + out.indPredicted, (100 + 300 + 200) * 100 / 50);
  // And the baseline-based swing result is exactly what the old call returns, so this really is a choice.
  const without = estimateArea({ ...area, indReal: 0 }, baseline, 1);
  assert.equal(without.usedFallback, false);
  assert.ok(without.historical);
});

test("estimateArea is unchanged for a race with no third candidate, key for key", () => {
  const base = { demReal: 100, repReal: 300, percentIn: 50, totalVotes: 410 };
  const baseline = { demShare: 0.45, repShare: 0.55, votes: 2000, year: 2020 };
  for (const entry of [baseline, null, undefined]) {
    const plain = estimateArea(base, entry, 0.6);
    for (const absent of [{}, { indReal: 0 }, { indReal: undefined }, { indReal: NaN }, { indReal: -5 }]) {
      assert.deepEqual(estimateArea({ ...base, ...absent }, entry, 0.6), plain);
    }
    assert.ok(!("indPredicted" in plain) && !("fallbackReason" in plain));
  }
  assert.deepEqual(estimateArea({ ...base, percentIn: 100 }, baseline, 1),
    { demPredicted: 100, repPredicted: 300, noData: false, usedFallback: false });
  assert.deepEqual(estimateArea({ ...base, percentIn: 0 }, baseline, 1),
    { demPredicted: null, repPredicted: null, noData: true, usedFallback: false });
});

test("a fully counted area and a no-data area with a third candidate", () => {
  const done = estimateArea({ demReal: 5, repReal: 6, indReal: 7, percentIn: 100, totalVotes: 20 }, null, 1);
  assert.deepEqual(done, { demPredicted: 5, repPredicted: 6, noData: false, usedFallback: false, indPredicted: 7 });
  const none = estimateArea({ demReal: 0, repReal: 0, indReal: 7, percentIn: 0, totalVotes: 0 }, null, 1);
  assert.equal(none.noData, true);
  assert.ok(!("indPredicted" in none));
});

test("turnoutBasis counts the third candidate's ballots, so a strong independent is not a turnout collapse", () => {
  const entries = [["1", { votes: 100000 }], ["2", { votes: 100000 }]];
  const areas = [{ fips: "1", demPredicted: 30000, repPredicted: 30000, indPredicted: 40000 }];
  const basis = turnoutBasis(areas, entries);
  assert.equal(basis.projected, 100000);
  assert.equal(basis.ratio, 1);                       // not 0.6
  const twoParty = turnoutBasis([{ fips: "1", demPredicted: 30000, repPredicted: 30000 }], entries);
  assert.equal(twoParty.ratio, 0.6);                  // unchanged for a race without one
});

test("the model is chosen by the RACE: a county where the third candidate has no votes is still flat", () => {
  const baseline = { demShare: 0.45, repShare: 0.55, votes: 2000, year: 2020 };
  const county = { demReal: 100, repReal: 300, indReal: 0, independentRace: true, percentIn: 50, totalVotes: 400 };
  const out = estimateArea(county, baseline, 1);
  assert.equal(out.usedFallback, true);
  assert.equal(out.fallbackReason, "independent");
  assert.equal(out.indPredicted, 0);
  assert.equal(out.demPredicted, 200);                    // flat, not the swing model
  assert.equal(estimateArea({ ...county, percentIn: 100 }, baseline, 1).indPredicted, 0);
  // ... and a race without a contender is untouched by the flag being false or absent.
  for (const flag of [false, undefined]) {
    assert.deepEqual(estimateArea({ ...county, independentRace: flag }, baseline, 1),
      estimateArea({ demReal: 100, repReal: 300, percentIn: 50, totalVotes: 400 }, baseline, 1));
  }
});
