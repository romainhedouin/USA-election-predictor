"""A third candidate in the data model: contender or not, and what that changes.

Feeds map.html's own buildRaceData synthetic CSV rows (exported on
window.__mapTest), so there is no network and no CSV file to keep in step. What
these guard, in order of importance:

  * a race WITHOUT a contender is exactly what it was before the third candidate
    existed - same projections, same total, same call - including when an older
    CSV has no independent columns at all;
  * the 5%-and-1,000-vote line, on both sides of the boundary, decided once per
    race (so a state's counties are all projected by the same model);
  * a contender race: flat three-way projection, a total that includes them, and
    leaders and calls that can now be purple.
"""

import pytest

from conftest import make_driver, wait_ready

RUN = """
const [rows, race, historical, countyReference] = arguments;
const data = window.__mapTest.buildRaceData(rows, race, historical, countyReference, null);
const out = {};
for (const [key, agg] of data.stateAggregates) {
  const status = window.__mapTest.stateStatusFor(data, key);
  const areas = (data.areasByState.get(key) || []).map(a => ({
    name: a.name, indReal: a.indReal, indPredicted: a.indPredicted ?? null,
    demPredicted: a.demPredicted, repPredicted: a.repPredicted,
    usedFallback: a.usedFallback, fallbackReason: a.fallbackReason ?? null,
  }));
  out[key] = {
    demReal: agg.demReal, repReal: agg.repReal, indReal: agg.indReal,
    demPredicted: agg.demPredicted, repPredicted: agg.repPredicted, indPredicted: agg.indPredicted,
    ballotEstimate: agg.ballotEstimate, independent: agg.independent,
    call: status.call, predictedLeader: status.predictedLeader, mismatch: status.mismatch,
    percentIn: status.percentIn, statusIndependent: status.independent, areas,
    allFinite: [agg.demReal, agg.repReal, agg.indReal, agg.demPredicted, agg.repPredicted, agg.indPredicted,
                agg.ballotEstimate, status.percentIn].every(Number.isFinite),
  };
}
return out;"""


def row(fips, name, pct, votes, d, r, i=None, state="Kentucky", geography="counties", expected=0,
        dname="D", rname="R", iname="Independent", iparty=""):
    """A CSV row as parseRaceCsv would hand it over. i=None writes no independent
    columns at all, like a CSV from before they existed."""
    out = {"State": state, "Area": name, "FIPS": fips, "Geography": geography,
           "State Total Expected": str(expected), "Total Votes": str(votes), "Percent In": str(pct),
           "Democrat Real": str(d), "Republican Real": str(r), "Democrat Name": dname, "Republican Name": rname}
    if i is not None:
        out.update({"Independent Real": str(i), "Independent Name": iname if i else "", "Independent Party": iparty})
    return out


def county_history(votes=100000):
    return {f"2100{n}": {"demShare": 0.4, "repShare": 0.6, "votes": votes, "year": 2024} for n in range(1, 5)}


def three_reporting(d, r, i, votes=None, **kw):
    """3 of the 4 counties, each 50% in, counting d / r / i votes: each projects to 2 x (d+r+i)."""
    votes = votes if votes is not None else d + r + i
    return [row(f"2100{n}", f"County {n}", 50, votes, d, r, i, **kw) for n in (1, 2, 3)]


@pytest.fixture(scope="module")
def driver(base_url):
    d = make_driver(1440, 900, False)
    d.get(f"{base_url}/map.html")
    wait_ready(d)
    yield d
    d.quit()


def run(driver, rows, race="president", historical=None, county_reference=None):
    return driver.execute_script(RUN, rows, race, historical if historical is not None else {}, county_reference)


KY = "kentucky"
COMPARED = ("demReal", "repReal", "demPredicted", "repPredicted", "ballotEstimate", "call",
            "predictedLeader", "mismatch", "percentIn")


def same_as_without(driver, with_i, without_i, race="president", historical=None, county_reference=None, key=KY):
    a = run(driver, with_i, race, historical, county_reference)[key]
    b = run(driver, without_i, race, historical, county_reference)[key]
    assert {f: a[f] for f in COMPARED} == {f: b[f] for f in COMPARED}
    assert [(x["demPredicted"], x["repPredicted"], x["usedFallback"]) for x in a["areas"]] == \
           [(x["demPredicted"], x["repPredicted"], x["usedFallback"]) for x in b["areas"]]
    return a


# ----------------------------------------------------------------- unchanged

def test_a_csv_without_independent_columns_is_a_two_candidate_race(driver):
    out = run(driver, three_reporting(20000, 20000, None, votes=40000), "president", county_history())[KY]
    assert out["allFinite"] and out["indReal"] == 0 and out["indPredicted"] == 0
    assert out["independent"] is None
    assert all(a["indPredicted"] is None and a["fallbackReason"] is None for a in out["areas"])


def test_zero_columns_and_missing_columns_are_the_same_thing(driver):
    same_as_without(driver, three_reporting(20000, 20000, 0, votes=40000),
                    three_reporting(20000, 20000, None, votes=40000), "president", county_history())


def test_an_independent_under_five_percent_is_ignored_entirely(driver):
    # 4.9% of the 100,000 votes counted per county: 4,900 votes, well over the floor, just under the share.
    small = three_reporting(47500, 47600, 4900, votes=100000)
    out = same_as_without(driver, small, three_reporting(47500, 47600, None, votes=100000), "president", county_history())
    assert out["independent"] is None and out["indReal"] == 0
    assert not any(a["usedFallback"] for a in out["areas"]), "the swing model must still run: a 4.9% independent changes nothing"


def test_an_independent_under_the_vote_floor_is_ignored_even_at_a_high_share(driver):
    # 30% of a tiny count is still only 600 votes in the state: not enough to switch models.
    tiny = [row("21001", "A", 50, 2000, 700, 700, 200), row("21002", "B", 50, 2000, 700, 700, 200)]
    out = same_as_without(driver, tiny, [row("21001", "A", 50, 2000, 700, 700), row("21002", "B", 50, 2000, 700, 700)],
                          "president", county_history())
    assert out["independent"] is None


def test_the_five_percent_line_is_inclusive(driver):
    # Exactly 5.0% (5,000 of 100,000, per county) is a contender; one vote less per county is not.
    at = run(driver, three_reporting(47500, 47500, 5000, votes=100000), "president", county_history())[KY]
    assert at["independent"] is not None and at["independent"]["share"] == pytest.approx(0.05)
    below = run(driver, three_reporting(47500, 47501, 4999, votes=100000), "president", county_history())[KY]
    assert below["independent"] is None


def test_finished_and_empty_races_are_unchanged(driver):
    done = [row(f"2100{n}", f"County {n}", 100, 100000, 40000, 40000, 20000) for n in range(1, 5)]
    out = run(driver, done, "president", county_history())[KY]
    assert out["allFinite"] and out["percentIn"] == pytest.approx(100)
    assert (out["demPredicted"], out["repPredicted"], out["indPredicted"]) == (160000, 160000, 80000)
    assert run(driver, [], "president", county_history()) == {}
    assert run(driver, [row("21001", "A", 0, 0, 0, 0, 0)], "president", county_history()) == {}


# ---------------------------------------------------------------- contender

def test_a_contender_is_projected_flat_beside_both_parties(driver):
    # Per county: D 15k, R 25k, I 20k counted at 50% in -> 30k / 50k / 40k projected.
    out = run(driver, three_reporting(15000, 25000, 20000), "president", county_history())[KY]
    assert out["independent"]["share"] == pytest.approx(1 / 3)
    assert (out["demReal"], out["repReal"], out["indReal"]) == (45000, 75000, 60000)
    assert (out["demPredicted"], out["repPredicted"], out["indPredicted"]) == (90000, 150000, 120000)
    assert out["predictedLeader"] == "R" and out["allFinite"]
    for a in out["areas"]:
        assert a["usedFallback"] is True and a["fallbackReason"] == "independent"
        assert (a["demPredicted"], a["repPredicted"], a["indPredicted"]) == (30000, 50000, 40000)


def test_the_expected_total_includes_the_contender(driver):
    # Projected 360k (3 counties x 120k, all three candidates) against 300k last time: x1.2, so the
    # silent fourth county (100k last time) is worth 120k - the same 480k as without an independent.
    out = run(driver, three_reporting(15000, 25000, 20000), "president", county_history())[KY]
    assert out["ballotEstimate"] == pytest.approx(480000)
    # The same race with the independent's votes ignored would look like a turnout collapse; it must not.
    assert out["ballotEstimate"] > out["demPredicted"] + out["repPredicted"] + out["indPredicted"]


def test_a_statewide_race_gets_the_same_total_from_president_weights(driver):
    historical = {"Kentucky": {"demShare": 0.4, "repShare": 0.6, "votes": 400000, "year": 2020}}
    out = run(driver, three_reporting(15000, 25000, 20000), "senate", historical, county_history())[KY]
    assert out["ballotEstimate"] == pytest.approx(480000)
    assert out["independent"] is not None and out["allFinite"]


def test_the_decision_is_made_per_race_not_per_county(driver):
    # Independent: 40% in county 1, 0% in the others - but 13.3% of the state: everyone is a contender,
    # so county 2 and 3 (no independent votes there) are projected by the same flat model.
    rows = [row("21001", "A", 50, 60000, 18000, 18000, 24000),
            row("21002", "B", 50, 60000, 30000, 30000, 0),
            row("21003", "C", 50, 60000, 30000, 30000, 0)]
    out = run(driver, rows, "president", county_history())[KY]
    assert out["independent"] is not None
    assert [a["usedFallback"] for a in out["areas"]] == [True, True, True]
    assert [a["indReal"] for a in out["areas"]] == [24000, 0, 0]


def test_two_states_of_one_race_are_decided_separately(driver):
    nebraska = [row("31001", "A", 50, 100000, 0, 55000, 40000, state="Nebraska", dname="")]
    ohio = [row("39001", "A", 50, 100000, 50000, 48000, 2000, state="Ohio")]
    out = run(driver, nebraska + ohio, "senate", {})
    assert out["nebraska"]["independent"] is not None and out["ohio"]["independent"] is None
    assert out["ohio"]["indReal"] == 0 and out["ohio"]["indPredicted"] == 0


def test_no_democrat_and_a_strong_independent_nebraska_like(driver):
    rows = [row("31001", "Adams", 95, 11034, 0, 6670, 3750, state="Nebraska", dname="", rname="Pete Ricketts",
                iname="Dan Osborn", expected=20000),
            row("31003", "Antelope", 95, 2740, 0, 2175, 422, state="Nebraska", dname="", rname="Pete Ricketts",
                iname="Dan Osborn", expected=20000)]
    out = run(driver, rows, "senate", {})["nebraska"]
    assert out["independent"]["name"] == "Dan Osborn"
    assert out["independent"]["share"] == pytest.approx((3750 + 422) / (11034 + 2740))
    assert out["predictedLeader"] == "R" and out["demReal"] == 0 and out["demPredicted"] == 0
    assert out["allFinite"]


def test_the_independent_can_lead_and_so_can_disagree_with_the_projection(driver):
    # County 1 has counted a lot (independent ahead); county 2 has barely started, and is strongly
    # Republican, so the projection flips the raw leader: the mismatch outline must work for purple too.
    rows = [row("21001", "A", 90, 90000, 10000, 30000, 50000),
            row("21002", "B", 10, 10000, 1000, 8000, 1000)]
    out = run(driver, rows, "president", {})[KY]
    assert out["indReal"] > out["repReal"]                       # raw leader: the independent
    assert out["predictedLeader"] in ("I", "R")
    assert out["mismatch"] == (out["predictedLeader"] != "I")


def test_a_house_district_is_one_race_with_its_own_contender(driver):
    rows = [row("1601", "Idaho District 1", 97, 310400, 24832, 124160, 161408, state="Idaho", geography="districts",
                expected=320000)]
    out = run(driver, rows, "house", {})["1601"]
    assert out["predictedLeader"] == "I" and out["call"] == "certain"
    assert out["independent"]["share"] == pytest.approx(161408 / 310400)
    small = [row("5600", "Wyoming At-Large", 20, 56000, 13580, 40740, 1680, state="Wyoming", geography="districts")]
    assert run(driver, small, "house", {})["5600"]["independent"] is None


def test_states_reported_below_county_level_work_too(driver):
    rows = [row("", f"Town {n}", 50, 60000, 20000, 20000, 20000, state="Maine", geography="townships") for n in range(3)]
    out = run(driver, rows, "governor", {"Maine": {"votes": 300000, "year": 2022}}, county_history())["maine"]
    assert out["independent"] is not None and out["allFinite"]
    assert out["ballotEstimate"] >= out["demPredicted"] + out["repPredicted"] + out["indPredicted"]


def test_the_party_label_and_name_come_from_the_first_row_that_has_them(driver):
    rows = three_reporting(15000, 25000, 20000, iname="Colt Shelby", iparty="Libertarian")
    out = run(driver, rows, "president", county_history())[KY]
    assert out["independent"]["name"] == "Colt Shelby" and out["independent"]["party"] == "Libertarian"
    assert out["statusIndependent"]["name"] == "Colt Shelby"


# ------------------------------------------------------------------- calls

def test_a_close_three_way_race_is_called_by_plurality_not_majority(driver):
    # 45 / 40 / 15, fully counted: nobody has 50%, but the leader has won.
    rows = [row(f"2100{n}", f"County {n}", 100, 100000, 45000, 40000, 15000) for n in (1, 2, 3, 4)]
    out = run(driver, rows, "president", county_history())[KY]
    assert out["predictedLeader"] == "D" and out["call"] == "certain"


def test_the_leader_is_not_called_while_the_runner_up_can_still_catch_up(driver):
    # Same split but only half the vote is in, and the expected total is the projected one.
    rows = [row(f"2100{n}", f"County {n}", 50, 50000, 22500, 20000, 7500) for n in (1, 2, 3, 4)]
    out = run(driver, rows, "president", county_history())[KY]
    assert out["predictedLeader"] == "D"
    assert out["call"] in ("projected", "unprojected")
    assert out["call"] != "certain"
