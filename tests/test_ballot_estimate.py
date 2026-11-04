"""A state's expected total ballots follows how this cycle's turnout is running.

The estimate used to be last cycle's size for every county that had not
reported (and, for Senate/Governor, last cycle's whole statewide total), however
this cycle's turnout was running. The numbers here are the worked example from
the design: a 4-county state where every county cast 100k last time, 3 have
reported, and each projects to 120k.

Feeds buildRaceData synthetic rows directly (exported on window.__mapTest), so
there is no network and no CSV to keep in step. Beyond the new total, what these
guard is everything that must NOT move: the D/R projections, states with nothing
to compare, finished states, townships, and districts.
"""

import pytest

from conftest import make_driver, wait_ready

HEADER = ["State", "Area", "FIPS", "Geography", "State Total Expected", "Total Votes", "Percent In",
          "Democrat Real", "Republican Real", "Democrat Name", "Republican Name"]

RUN = """
const [rows, race, historical, countyReference] = arguments;
const data = window.__mapTest.buildRaceData(rows, race, historical, countyReference, null);
const key = race === 'house' ? rows[0].FIPS : rows[0].State.toLowerCase();
const agg = data.stateAggregates.get(key);
const status = window.__mapTest.stateStatusFor(data, key);
return {
  ballotEstimate: agg.ballotEstimate, demPredicted: agg.demPredicted, repPredicted: agg.repPredicted,
  percentIn: status.percentIn, turnout: agg.turnout || null,
  explain: window.__mapTest.explainTotals(agg, data.areasByState.get(key), {}),
};"""


def row(fips, name, pct, votes, state="Kentucky", geography="counties", expected=0):
    return {"State": state, "Area": name, "FIPS": fips, "Geography": geography,
            "State Total Expected": str(expected), "Total Votes": str(votes), "Percent In": str(pct),
            "Democrat Real": str(votes // 2), "Republican Real": str(votes // 2),
            "Democrat Name": "D", "Republican Name": "R"}


def county_history(votes=100000):
    return {f"2100{i}": {"demShare": 0.4, "repShare": 0.6, "votes": votes, "year": 2024} for i in range(1, 5)}


# 3 of the 4 counties are 50% in with 60k counted: each projects to exactly 120k.
THREE_REPORTING = [row(f"2100{i}", f"County {i}", 50, 60000) for i in (1, 2, 3)]


@pytest.fixture(scope="module")
def driver(base_url):
    d = make_driver(1440, 900, False)
    d.get(f"{base_url}/map.html")
    wait_ready(d)
    yield d
    d.quit()


def run(driver, rows, race, historical, county_reference=None):
    return driver.execute_script(RUN, rows, race, historical, county_reference)


def test_president_scales_the_unreported_county_by_the_turnout_so_far(driver):
    # Was 460k: the 4th county kept its raw 100k. Its neighbours are running 20% up.
    out = run(driver, THREE_REPORTING, "president", county_history())
    assert out["ballotEstimate"] == pytest.approx(480000)
    assert out["demPredicted"] + out["repPredicted"] == pytest.approx(360000)  # the projection itself is untouched
    assert out["turnout"]["kind"] == "scale"
    assert out["turnout"]["trust"] == 1
    assert out["turnout"]["ratio"] == pytest.approx(1.2)


@pytest.mark.parametrize("race", ["senate", "governor"])
def test_statewide_races_get_the_same_answer_instead_of_the_flat_historical_total(driver, race):
    # Was 400k (the whole previous statewide total, ignoring the 20% growth).
    historical = {"Kentucky": {"demShare": 0.4, "repShare": 0.6, "votes": 400000, "year": 2022}}
    out = run(driver, THREE_REPORTING, race, historical, county_history())
    assert out["ballotEstimate"] == pytest.approx(480000)
    assert out["demPredicted"] + out["repPredicted"] == pytest.approx(360000)
    assert out["turnout"]["kind"] == "share"


def test_turnout_below_the_baseline_shrinks_the_estimate(driver):
    # A midterm against a presidential-year baseline: 70% of last time.
    rows = [row(f"2100{i}", f"County {i}", 50, 35000) for i in (1, 2, 3)]
    historical = {"Kentucky": {"demShare": 0.4, "repShare": 0.6, "votes": 400000, "year": 2020}}
    out = run(driver, rows, "senate", historical, county_history())
    assert out["ballotEstimate"] == pytest.approx(280000)  # 210k projected over a 75% share
    assert out["ballotEstimate"] >= out["demPredicted"] + out["repPredicted"]


def test_one_small_early_county_barely_moves_the_estimate(driver):
    # One county holding 1% of last time's vote reports at 3x its old size: trust is ~0.03.
    history = {"21001": {"demShare": 0.4, "repShare": 0.6, "votes": 1000, "year": 2024},
               "21002": {"demShare": 0.4, "repShare": 0.6, "votes": 99000, "year": 2024}}
    out = run(driver, [row("21001", "Tiny", 50, 1500)], "president", history)
    assert out["ballotEstimate"] < 99000 * 1.1 + 3000


def test_nothing_to_compare_against_leaves_today_s_numbers_alone(driver):
    # No baseline at all: just the projection, no silent counties to add.
    out = run(driver, THREE_REPORTING, "president", {})
    assert out["ballotEstimate"] == pytest.approx(360000)
    assert out["turnout"] is None
    # Statewide race with no President weights: the historical total, floored at the projection.
    historical = {"Kentucky": {"demShare": 0.4, "repShare": 0.6, "votes": 400000, "year": 2022}}
    out = run(driver, THREE_REPORTING, "senate", historical, None)
    assert out["ballotEstimate"] == pytest.approx(400000)
    assert out["turnout"] is None


def test_a_finished_state_is_unchanged(driver):
    rows = [row(f"2100{i}", f"County {i}", 100, 100000) for i in range(1, 5)]
    out = run(driver, rows, "president", county_history())
    assert out["ballotEstimate"] == pytest.approx(400000)  # nothing silent, nothing to scale
    assert out["percentIn"] == pytest.approx(100)


def test_states_reported_by_township_are_unchanged(driver):
    # No county FIPS: there is no per-county baseline to compare, so no scaling.
    rows = [row("", f"Town {i}", 50, 60000, state="Maine", geography="townships") for i in range(3)]
    historical = {"Maine": {"demShare": 0.4, "repShare": 0.6, "votes": 400000, "year": 2020}}
    out = run(driver, rows, "senate", historical, county_history())
    assert out["ballotEstimate"] == pytest.approx(400000)
    assert out["turnout"] is None


def test_a_house_district_is_unchanged(driver):
    # One row IS the whole district: its own last result, floored at its projection.
    rows = [row("2103", "Kentucky District 3", 50, 100000, geography="districts")]
    out = run(driver, rows, "house", {"2103": {"demShare": 0.6, "repShare": 0.4, "votes": 300000, "year": 2024}})
    assert out["ballotEstimate"] == pytest.approx(300000)
    assert out["turnout"] is None


def test_the_working_shows_the_scale_it_applied(driver):
    county = run(driver, THREE_REPORTING, "president", county_history())["explain"]
    assert "Turnout so far" in county and "&times;1.200" in county
    statewide = run(driver, THREE_REPORTING, "senate",
                    {"Kentucky": {"demShare": 0.4, "repShare": 0.6, "votes": 400000, "year": 2022}},
                    county_history())["explain"]
    assert "Turnout so far" in statewide and "480,000" in statewide
    # A state with nothing to compare keeps the old wording.
    plain = run(driver, THREE_REPORTING, "president", {})["explain"]
    assert "Turnout so far" not in plain
