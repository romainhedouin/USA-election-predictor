"""The third candidate: one non-Democrat, non-Republican per race, end to end
through the backend (no browser, no network).

Why this exists: the map used to see only a Democrat and a Republican, so a
strong independent was invisible - Nebraska's Dan Osborn held 43.7% of a
715,000-vote count and the page showed "Democrat 0" and called the race
certain for the Republican. These tests pin the rules that keep it right:

  * the candidate is the strongest non-major one, not whoever NBC labels
    independent (Osborn is coded "other") and not the sum of a party code
    (Nebraska's "other" adds Osborn to three candidates under 3% each);
  * write-in lines are never a candidate;
  * the same person is reported in every county of a race;
  * the CSV only ever grows at the right-hand end, and old rows are padded.

The payload fixtures copy the shapes NBC really returns (checked live):
candidates carry a numeric `code` and an `order` in state feeds, and neither
in the House feed.
"""

import csv
import io
import subprocess
import sys
from pathlib import Path

import pytest

import fetch_results
import nbc_api
import server
from races import CSV_HEADER, HOUSE_DISTRICTS

REPO = Path(__file__).resolve().parent.parent

LEGACY_HEADER = [
    "State", "Area", "FIPS", "Geography", "State Total Expected", "Total Votes", "Percent In",
    "Democrat Real", "Republican Real", "Democrat Name", "Republican Name",
]


def cand(party, name, votes, code=None, order=None):
    c = {"party": party, "name": name, "votes": votes}
    if code is not None:
        c["code"] = code
    if order is not None:
        c["order"] = order
    return c


# --- Nebraska, as NBC lists it: Osborn is coded "other", like three tiny
# candidates and the write-in line; there is no Democrat. -------------------
def nebraska_candidates(scale=1.0):
    def v(n):
        return round(n * scale)
    return [
        cand("gop", "Pete Ricketts", v(6670), code=51566, order=1),
        cand("other", "Dan Osborn", v(3750), code=168007, order=2),
        cand("other", "Mike Marvin", v(206), code=175130, order=3),
        cand("other", "Robin Richards", v(264), code=129622, order=4),
        cand("other", "Chuck Conboy", v(144), code=179197, order=5),
        cand("other", "Total Write-ins", v(12)),
    ]


NEBRASKA = {
    "stateName": "Nebraska", "geography": "counties", "lastModified": "2026-11-04T09:00:00Z",
    "races": [{
        "areas": [
            {"name": "Adams", "percentIn": 100.0, "votes": 11046, "candidates": nebraska_candidates()},
            {"name": "Antelope", "percentIn": 90.0, "votes": 2000,
             "candidates": [cand("gop", "Pete Ricketts", 1500, 51566), cand("other", "Dan Osborn", 400, 168007)]},
        ],
        "summary": {
            "votes": 13046, "estimatedVotesRemaining": {"value": 200}, "percentIn": 99.0,
            "candidates": nebraska_candidates(),
        },
    }],
}


def run_state(monkeypatch, payload, slug="nebraska", race="senate"):
    monkeypatch.setattr(nbc_api, "_get", lambda url: payload)
    return nbc_api.state_results(slug, race, 2026)


# ---------------------------------------------------------------- selection

def test_the_independent_is_the_strongest_non_major_candidate_not_the_sum_of_a_party_code(monkeypatch):
    result = run_state(monkeypatch, NEBRASKA)
    assert result["independent"] == {"name": "Dan Osborn", "party": "", "code": 168007}
    adams, antelope = result["areas"]
    # Osborn alone, not the "other" bucket (which also holds the three small candidates and write-ins).
    assert adams["independent_votes"] == 3750
    assert adams["by_party"]["other"] == 3750 + 206 + 264 + 144 + 12
    assert antelope["independent_votes"] == 400
    # The two-party figures the page already depends on are untouched.
    assert result["candidates"]["gop"] == "Pete Ricketts"
    assert "dem" not in result["candidates"]
    assert adams["by_party"]["gop"] == 6670


def test_a_candidate_missing_from_an_area_gets_zero_there(monkeypatch):
    payload = {**NEBRASKA, "races": [{**NEBRASKA["races"][0], "areas": [
        {"name": "Adams", "percentIn": 100.0, "votes": 100, "candidates": [cand("gop", "Pete Ricketts", 100, 51566)]},
    ]}]}
    assert run_state(monkeypatch, payload)["areas"][0]["independent_votes"] == 0


@pytest.mark.parametrize("name", [
    "Total Write-ins", " Total Write-ins", "Jane Doe (write-in)", "JANE DOE (Write In)", "None of These Candidates",
])
def test_write_in_lines_are_never_a_candidate(name):
    assert nbc_api._is_write_in(name)
    # Even when the write-in line out-polls every real third candidate.
    candidates = [cand("gop", "R", 500), cand("other", name, 900), cand("other", "Real Minor", 20)]
    assert nbc_api._independent_of(candidates)["name"] == "Real Minor"
    assert nbc_api._independent_of([cand("gop", "R", 500), cand("other", name, 900)]) is None


def test_real_names_that_merely_contain_other_words_are_not_write_ins():
    assert not nbc_api._is_write_in("Dan Osborn")
    assert not nbc_api._is_write_in("Ryan Wright")
    assert not nbc_api._is_write_in("")
    assert not nbc_api._is_write_in(None)


def test_two_independents_in_one_race_only_the_leader_is_carried():
    candidates = [cand("gop", "Jim Risch", 600), cand("ind", "Todd Achilles", 340, 1, 2), cand("ind", "Natalie Fleming", 70, 2, 3)]
    assert nbc_api._independent_of(candidates) == {"name": "Todd Achilles", "party": "Independent", "code": 1}


def test_no_candidate_beyond_the_two_majors_means_none():
    assert nbc_api._independent_of([cand("dem", "D", 5), cand("gop", "R", 4)]) is None
    assert nbc_api._independent_of([]) is None
    # A third candidate with no votes yet is not carried: nothing to show, and
    # the next refresh picks whoever has actually polled.
    assert nbc_api._independent_of([cand("dem", "D", 5), cand("gop", "R", 4), cand("lib", "L", 0)]) is None


def test_democrat_versus_democrat_has_no_independent():
    """California's top-two: two Democrats are one party code, not a third candidate."""
    assert nbc_api._independent_of([cand("dem", "A Dem", 60), cand("dem", "B Dem", 40)]) is None


def test_ties_break_by_nbcs_listing_order_then_name():
    a, b = cand("ind", "Zed", 100, order=2), cand("ind", "Amy", 100, order=1)
    assert nbc_api._independent_of([a, b])["name"] == "Amy"           # lower order wins
    c, d = cand("ind", "Zed", 100), cand("ind", "Amy", 100)
    assert nbc_api._independent_of([c, d])["name"] == "Amy"           # no order: by name
    assert nbc_api._independent_of([d, c]) == nbc_api._independent_of([c, d])  # input order does not matter


def test_party_labels_only_where_the_code_says_something():
    label = lambda code: nbc_api._independent_of([cand(code, "X", 10)])["party"]
    assert label("ind") == "Independent"
    assert label("Lib") == "Libertarian"
    assert label("NPP") == "No party preference"
    assert label("Una") == "Unaffiliated"
    assert label("other") == ""     # Osborn: NBC's catch-all, so name only
    assert label("Oth") == ""
    assert label("XYZ") == ""        # never guessed


# ----------------------------------------------------------------- matching

def test_votes_match_on_nbc_code_first_so_a_respelled_name_still_counts():
    chosen = {"name": "Dan Osborn", "party": "", "code": 7}
    assert nbc_api._votes_for([cand("other", "Daniel Osborn", 90, code=7)], chosen) == 90


def test_votes_fall_back_to_the_name_ignoring_case_and_spacing():
    chosen = {"name": "Kevin Kiley", "party": "", "code": None}
    assert nbc_api._votes_for([cand("NPP", "  kevin   KILEY ", 42)], chosen) == 42
    assert nbc_api._votes_for([cand("NPP", "Kevin Other", 42)], chosen) == 0
    assert nbc_api._votes_for([cand("Dem", "Kevin Kiley", 42, code=None)], None) == 0


def test_a_same_named_candidate_with_a_different_code_is_not_the_same_person():
    chosen = {"name": "Alex Lee", "party": "", "code": 1}
    assert nbc_api._votes_for([cand("ind", "Alex Lee", 5, code=2), cand("ind", "Alex Lee", 9, code=1)], chosen) == 9


# ------------------------------------------------------------------ the House

def house_payload(districts):
    return {"lastModified": "2026-11-04T09:00:00Z",
            "mapData": {geoid: {"tooltip": {"percentIn": 50.0, "totalVote": sum(c["votes"] for c in cands),
                                            "remainingVote": 1000, "candidates": cands}}
                        for geoid, cands in districts.items()}}


def test_house_chooses_a_candidate_per_district_across_nbcs_many_party_codes(monkeypatch):
    payload = house_payload({
        # Kiley is "NPP" in this feed; Hill "NP"; Milleron "Ind"; an "Oth" and a write-in line trail.
        "0606": [cand("Dem", "A Dem", 30), cand("Rep", "A Rep", 20), cand("NPP", "Kevin Kiley", 40), cand("Oth", " Total Write-ins", 90)],
        "0201": [cand("Rep", "R", 30), cand("NP", "Bill Hill", 40), cand("Dem", "D", 25)],   # NBC numbers Alaska's seat 0201
        "2501": [cand("Dem", "D", 50), cand("Ind", "Nadia Milleron", 30), cand("Lib", "L", 5)],
        "0604": [cand("Dem", "One Dem", 60), cand("Dem", "Two Dem", 40)],      # top-two, same party
        "5600": [cand("Rep", "R", 70), cand("Dem", "D", 25), cand("Lib", "Shawn Johnson", 5)],
    })
    monkeypatch.setattr(nbc_api, "_get", lambda url: payload)
    districts, _ = nbc_api.house_results(2026)
    by_geoid = {d["geoid"]: d for d in districts}
    assert by_geoid["0606"]["independent"]["name"] == "Kevin Kiley" and by_geoid["0606"]["independent_votes"] == 40
    assert by_geoid["0200"]["independent"]["name"] == "Bill Hill"          # normalised to the Census GEOID
    assert by_geoid["2501"]["independent"] == {"name": "Nadia Milleron", "party": "Independent", "code": None}
    assert by_geoid["0604"]["independent"] is None and by_geoid["0604"]["independent_votes"] == 0
    assert by_geoid["5600"]["independent"]["party"] == "Libertarian"
    # Untouched two-party data: Republican comes from "Rep", summed by code.
    assert by_geoid["0606"]["by_party"]["gop"] == 20 and by_geoid["0606"]["candidates"]["gop"] == "A Rep"


def test_district_results_carries_the_independent_for_the_drilldown(monkeypatch):
    payload = {"underlyingGeographies": "counties", "lastModified": "x", "races": [{
        "areas": [
            {"name": "Alachua", "percentIn": 100.0, "votes": 900, "candidates": [
                cand("dem", "D Cand", 400, 1), cand("gop", "R Cand", 300, 2), cand("ind", "I Cand", 200, 3)]},
            {"name": "Baker", "percentIn": 50.0, "votes": 100, "candidates": [cand("dem", "D Cand", 60, 1)]},
        ],
        "summary": {"votes": 1000, "estimatedVotesRemaining": {"value": 0}, "percentIn": 100.0,
                    "candidates": [cand("dem", "D Cand", 460, 1), cand("gop", "R Cand", 300, 2), cand("ind", "I Cand", 200, 3)]},
    }]}
    monkeypatch.setattr(nbc_api, "_get", lambda url: payload)
    result = nbc_api.district_results("1201", 2026)
    assert result["independent"]["name"] == "I Cand"
    assert [a["independent_votes"] for a in result["areas"]] == [200, 0]


# ---------------------------------------------------------------- CSV rows

def test_state_rows_are_as_wide_as_the_header_and_carry_the_third_candidate(monkeypatch):
    rows = fetch_results.state_rows(run_state(monkeypatch, NEBRASKA))
    assert {len(r) for r in rows} == {len(CSV_HEADER)}
    adams = dict(zip(CSV_HEADER, rows[0]))
    assert adams["Democrat Real"] == 0 and adams["Democrat Name"] == ""        # no Democrat ran
    assert adams["Republican Real"] == 6670 and adams["Republican Name"] == "Pete Ricketts"
    assert adams["Independent Real"] == 3750 and adams["Independent Name"] == "Dan Osborn"
    assert adams["Independent Party"] == ""
    assert adams["Total Votes"] == 11046        # NBC's all-candidate count, unchanged


def test_a_race_with_only_the_two_majors_writes_zero_and_blanks(monkeypatch):
    rows = fetch_results.state_rows(run_state(monkeypatch, {
        "stateName": "Ohio", "geography": "counties", "lastModified": "x", "races": [{
            "areas": [{"name": "Adams", "percentIn": 50.0, "votes": 10,
                       "candidates": [cand("dem", "D", 4), cand("gop", "R", 6)]}],
            "summary": {"votes": 10, "estimatedVotesRemaining": {"value": 10}, "percentIn": 50.0,
                        "candidates": [cand("dem", "D", 4), cand("gop", "R", 6)]}}]}, slug="ohio"))
    assert rows[0][-3:] == [0, "", ""]


def test_house_rows_carry_the_third_candidate(monkeypatch):
    geoid = next(iter(HOUSE_DISTRICTS))
    payload = house_payload({geoid: [cand("Dem", "D", 30), cand("Rep", "R", 20), cand("Ind", "Ivy Independent", 45)]})
    monkeypatch.setattr(nbc_api, "_get", lambda url: payload)
    districts, _ = nbc_api.house_results(2026)
    rows = fetch_results.house_rows(districts)
    assert len(rows[0]) == len(CSV_HEADER)
    assert rows[0][-3:] == [45, "Ivy Independent", "Independent"]


def test_the_first_eleven_columns_never_move():
    """An old CSV on a deployed volume, and an old cached page, both rely on this."""
    assert CSV_HEADER[:11] == LEGACY_HEADER
    assert CSV_HEADER[11:] == ["Independent Real", "Independent Name", "Independent Party"]


def test_carried_forward_rows_from_an_old_csv_are_padded_to_the_new_width(tmp_path):
    served = tmp_path / "raw_data_senate.csv"
    with open(served, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(LEGACY_HEADER)                 # the schema every deployed volume has today
        writer.writerow(["Ohio", "Adams", "39001", "counties", 100, 50, 50.0, 20, 30, "D", "R"])
        writer.writerow(["Texas", "Harris", "48201", "counties", 100, 50, 50.0, 20, 30, "D", "R"])
    served.with_suffix(".meta.json").write_text('{"source": "live", "dataYear": "2026"}')
    rows = fetch_results.carried_forward_rows(served, {"ohio"}, "2026")
    assert len(rows) == 1 and len(rows[0]) == len(CSV_HEADER)
    assert rows[0][:11] == ["Ohio", "Adams", "39001", "counties", "100", "50", "50.0", "20", "30", "D", "R"]
    assert rows[0][11:] == ["0", "", ""]


def test_a_carried_forward_state_beside_fresh_rows_gives_a_rectangular_file(tmp_path):
    fresh = [["Texas", "Harris", "48201", "counties", 100, 50, 50.0, 20, 30, "D", "R", 5, "I", "Independent"]]
    served = tmp_path / "raw_data_senate.csv"
    served.write_text(";".join(LEGACY_HEADER) + "\nOhio;Adams;39001;counties;100;50;50.0;20;30;D;R\n")
    served.with_suffix(".meta.json").write_text('{"source": "live", "dataYear": "2026"}')
    out = tmp_path / "out.csv"
    fetch_results.write_csv(fresh + fetch_results.carried_forward_rows(served, {"ohio"}, "2026"), out)
    parsed = list(csv.reader(out.open(newline="", encoding="utf-8"), delimiter=";"))
    assert parsed[0] == CSV_HEADER and {len(r) for r in parsed} == {len(CSV_HEADER)}


def test_csv_round_trips_a_name_with_a_semicolon_and_a_quote(tmp_path):
    name = 'Jack "Jax" O\'Neil; Jr.'
    out = tmp_path / "raw_data_senate.csv"
    fetch_results.write_csv([["Ohio", "Adams", "39001", "counties", 1, 1, 50.0, 0, 0, "", "", 7, name, "Independent"]], out)
    row = list(csv.DictReader(out.open(newline="", encoding="utf-8"), delimiter=";"))[0]
    assert row["Independent Name"] == name and row["Independent Real"] == "7"
    assert server._looks_like_csv(out)


# ------------------------------------------------------------------ server

def _district_result(**extra):
    return {"geoid": "1201", "label": "Test District", "state": "Testland", "geography": "counties",
            "county_level": True, "total_expected": 1000, "percent_in": 100.0,
            "candidates": {"dem": "A", "gop": "B"}, "last_modified": None, **extra}


def _payload(monkeypatch, result):
    import json
    monkeypatch.setattr(server.nbc_api, "district_results", lambda geoid, cycle: result)
    server._district_cache.clear()
    try:
        status, body = server._house_district_payload(next(iter(server._KNOWN_DISTRICT_GEOIDS)))
    finally:
        server._district_cache.clear()
    assert status == 200
    return json.loads(body)


def test_district_proxy_hands_the_third_candidate_to_the_page(monkeypatch):
    payload = _payload(monkeypatch, _district_result(
        independent={"name": "I Cand", "party": "Independent", "code": 3},
        areas=[{"name": "Alachua", "fips": "12001", "percent_in": 100.0, "votes": 900,
                "by_party": {"dem": 400, "gop": 300}, "independent_votes": 200}]))
    assert payload["indName"] == "I Cand" and payload["indParty"] == "Independent"
    assert payload["areas"][0]["indReal"] == 200
    assert payload["areas"][0]["demReal"] == 400        # unchanged


def test_district_proxy_still_works_for_a_result_without_any_independent_fields(monkeypatch):
    """An older cached result, or a stub like test_server_district_cache's, has none of them."""
    payload = _payload(monkeypatch, _district_result(
        areas=[{"name": "Alachua", "fips": "12001", "percent_in": 100.0, "votes": 900, "by_party": {"dem": 4, "gop": 3}}]))
    assert payload["indName"] == "" and payload["indParty"] == ""
    assert payload["areas"][0]["indReal"] == 0


# --------------------------------------------------------------- mock data

def _mock(tmp_path, race):
    subprocess.run([sys.executable, str(REPO / "generate_mock_data.py"), "--race", race, "--out-dir", str(tmp_path)],
                   cwd=REPO, check=True, capture_output=True, text=True)
    name = {"president": "raw_data.csv"}.get(race, f"raw_data_{race}.csv")
    with open(tmp_path / name, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file, delimiter=";"))


@pytest.mark.parametrize("race", ["president", "senate", "governor", "house"])
def test_mock_rows_match_the_header_width(tmp_path, race):
    _mock(tmp_path, race)
    path = tmp_path / ("raw_data.csv" if race == "president" else f"raw_data_{race}.csv")
    with open(path, newline="", encoding="utf-8") as file:
        assert {len(r) for r in csv.reader(file, delimiter=";")} == {len(CSV_HEADER)}


def test_mock_president_has_no_independent_so_the_suite_keeps_a_contrast(tmp_path):
    assert all(row["Independent Real"] == "0" and not row["Independent Name"] for row in _mock(tmp_path, "president"))


def test_mock_races_exercise_every_independent_scenario(tmp_path):
    def share(rows, state):
        mine = [r for r in rows if r["State"] == state]
        return sum(int(r["Independent Real"]) for r in mine) / sum(int(r["Total Votes"]) for r in mine)

    senate = _mock(tmp_path, "senate")
    idaho = [r for r in senate if r["State"] == "Idaho"]
    assert all(r["Democrat Name"] == "" and r["Democrat Real"] == "0" for r in idaho)     # Nebraska-like
    assert 0.3 < share(senate, "Idaho") < 0.45
    assert 0 < share(senate, "Wyoming") < 0.05                                            # present but under the threshold

    governor = _mock(tmp_path, "governor")
    assert share(governor, "Wyoming") > 0.4                                                # an independent leads
    wy = [r for r in governor if r["State"] == "Wyoming"]
    assert sum(int(r["Independent Real"]) for r in wy) > sum(int(r["Republican Real"]) for r in wy)
    assert 0 < share(governor, "Vermont") < 0.05

    house = {r["FIPS"]: r for r in _mock(tmp_path, "house")}
    assert int(house["1601"]["Independent Real"]) > int(house["1601"]["Republican Real"])
    assert 0 < int(house["5600"]["Independent Real"]) / int(house["5600"]["Total Votes"]) < 0.05
    assert house["5000"]["Independent Name"] == ""
