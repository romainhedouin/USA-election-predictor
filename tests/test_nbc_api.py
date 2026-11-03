"""Unit tests for nbc_api.py's pure payload-normalisation logic. Plain
pytest, no Selenium/browser and no network - _get() is monkeypatched to
return a canned, real-shaped payload instead of hitting NBC.

Regression lock for the state_results()/district_results() readability
refactor (deduplicating their identical five-key summary block): these tests
pin down the exact returned dict for both functions against realistic input,
so the extraction can't quietly change a value or drop a key.
"""

import nbc_api

STATE_PAYLOAD = {
    "stateName": "Florida",
    "geography": "counties",
    "lastModified": "2026-11-03T22:00:00Z",
    "races": [
        {
            "areas": [
                {"name": "Alachua", "percentIn": 100.0, "votes": 1000,
                 "candidates": [{"party": "dem", "name": "A Dem", "votes": 600},
                                {"party": "gop", "name": "A Gop", "votes": 400}]},
                {"name": "Baker", "percentIn": 50.0, "votes": 200,
                 "candidates": [{"party": "dem", "name": "A Dem", "votes": 50},
                                {"party": "gop", "name": "A Gop", "votes": 150}]},
            ],
            "summary": {
                "votes": 1200,
                "estimatedVotesRemaining": {"value": 300},
                "percentIn": 80.0,
                "candidates": [
                    {"party": "dem", "name": "A Dem", "votes": 650},
                    {"party": "gop", "name": "A Gop", "votes": 550},
                ],
            },
        },
    ],
}

DISTRICT_PAYLOAD = {
    "underlyingGeographies": "counties",
    "lastModified": "2026-11-03T23:00:00Z",
    "races": [
        {
            "areas": [
                {"name": "Alachua", "percentIn": 100.0, "votes": 900,
                 "candidates": [{"party": "dem", "name": "D Cand", "votes": 500},
                                {"party": "gop", "name": "R Cand", "votes": 400}]},
            ],
            "summary": {
                "votes": 900,
                "estimatedVotesRemaining": {"value": 100},
                "percentIn": 90.0,
                "candidates": [
                    {"party": "dem", "name": "D Cand", "votes": 500},
                    {"party": "gop", "name": "R Cand", "votes": 400},
                ],
            },
        },
    ],
}


def test_state_results_normalises_a_real_shaped_payload(monkeypatch):
    monkeypatch.setattr(nbc_api, "_get", lambda url: STATE_PAYLOAD)

    result = nbc_api.state_results("florida", "president", 2026)

    assert result["state"] == "Florida"
    assert result["geography"] == "counties"
    assert result["county_level"] is True
    assert result["total_expected"] == 1200 + 300
    assert result["percent_in"] == 80.0
    assert result["last_modified"] == "2026-11-03T22:00:00Z"
    assert result["candidates"] == {"dem": "A Dem", "gop": "A Gop"}
    assert result["areas"] == [
        {"name": "Alachua", "fips": "12001", "percent_in": 100.0, "votes": 1000,
         "by_party": {"dem": 600, "gop": 400}},
        {"name": "Baker", "fips": "12003", "percent_in": 50.0, "votes": 200,
         "by_party": {"dem": 50, "gop": 150}},
    ]


def test_district_results_normalises_a_real_shaped_payload(monkeypatch):
    monkeypatch.setattr(nbc_api, "_get", lambda url: DISTRICT_PAYLOAD)

    result = nbc_api.district_results("1201", 2026)

    assert result["geoid"] == "1201"
    assert result["label"] == "Florida District 1"
    assert result["state"] == "Florida"
    assert result["geography"] == "counties"
    assert result["county_level"] is True
    assert result["total_expected"] == 900 + 100
    assert result["percent_in"] == 90.0
    assert result["last_modified"] == "2026-11-03T23:00:00Z"
    assert result["candidates"] == {"dem": "D Cand", "gop": "R Cand"}
    assert result["areas"] == [
        {"name": "Alachua", "fips": "12001", "percent_in": 100.0, "votes": 900,
         "by_party": {"dem": 500, "gop": 400}},
    ]


def test_state_and_district_results_share_the_same_summary_field_shape(monkeypatch):
    """Both functions build total_expected/percent_in/last_modified/candidates
    from a summary dict the same way - this is exactly the block the
    readability refactor extracts into a shared helper. Feeding the same
    summary/areas/lastModified through both should produce identical values
    for those shared keys, regardless of which function computed them."""
    shared_summary = {
        "votes": 500,
        "estimatedVotesRemaining": {"value": 50},
        "percentIn": 91.0,
        "candidates": [{"party": "dem", "name": "Same Dem", "votes": 300},
                        {"party": "gop", "name": "Same Gop", "votes": 200}],
    }
    state_payload = {
        "stateName": "Ohio", "geography": "counties", "lastModified": "same-ts",
        "races": [{"areas": [], "summary": shared_summary}],
    }
    district_payload = {
        "underlyingGeographies": "counties", "lastModified": "same-ts",
        "races": [{"areas": [], "summary": shared_summary}],
    }

    monkeypatch.setattr(nbc_api, "_get", lambda url: state_payload)
    state_result = nbc_api.state_results("ohio", "senate", 2026)

    monkeypatch.setattr(nbc_api, "_get", lambda url: district_payload)
    district_result = nbc_api.district_results("1201", 2026)

    shared_keys = ("total_expected", "percent_in", "last_modified", "candidates")
    for key in shared_keys:
        assert state_result[key] == district_result[key]


def test_state_slugs_drops_lieutenant_governor_pages_and_unopened_races(monkeypatch):
    """NBC's 2026 indexes link other offices and unopened races.

    The governor index carries lieutenant-governor pages ("alabama-lieutenant")
    that would otherwise be fetched and merged into the governor CSV, and
    Delaware's Senate row has no href before polls open (which used to crash
    the whole fetch).
    """
    index = {"tableData": {"states": [
        {"stateName": "Alabama", "href": "/politics/2026-elections/alabama-governor-results"},
        {"stateName": "Alabama", "href": "/politics/2026-elections/alabama-lieutenant-governor-results"},
        {"stateName": "Delaware", "href": None},
        {"stateName": "New Hampshire", "href": "/politics/2026-elections/new-hampshire-governor-results"},
    ]}}
    monkeypatch.setattr(nbc_api, "_get", lambda url: index)
    assert nbc_api.state_slugs("governor", "2026") == ["alabama", "new-hampshire"]


def test_state_slugs_from_a_senate_index(monkeypatch):
    index = {"tableData": {"states": [
        {"stateName": "Georgia", "href": "/politics/2026-elections/georgia-senate-results"},
        {"stateName": "Delaware", "href": None},
        {"stateName": "Arizona", "href": "/politics/2026-elections/arizona-governor-results"},
        {"stateName": "Ohio", "href": "/politics/2026-special-elections/ohio-senate-results"},
    ]}}
    monkeypatch.setattr(nbc_api, "_get", lambda url: index)
    assert nbc_api.state_slugs("senate", "2026") == ["georgia", "ohio"]
    assert nbc_api.state_slugs("governor", "2026") == ["arizona"]


def test_special_senate_races_use_the_special_elections_path(monkeypatch):
    urls = []
    monkeypatch.setattr(nbc_api, "_get_or_none", lambda url: urls.append(url))
    nbc_api.state_results("florida", "senate", "2026")
    nbc_api.state_results("georgia", "senate", "2026")
    nbc_api.state_results("florida", "governor", "2026")
    assert [u.split("/state-results/")[1] for u in urls] == [
        "2026-special-elections/florida-senate-results",
        "2026-elections/georgia-senate-results",
        "2026-elections/florida-governor-results",
    ]


def test_county_names_match_the_fips_table_across_nbc_spellings():
    """NBC's 2026 spellings differ from the 2024 names the table was frozen from."""
    def fips(state, name):
        return nbc_api._build_areas(
            [{"name": name}], nbc_api._fips_by_name(state, "counties"))[0]["fips"]

    assert fips("illinois", "De Witt") == "17039"          # spacing
    assert fips("new-mexico", "Doña Ana") == "35013"       # accent and case
    assert fips("virginia", "Fairfax city") == "51600"     # case
    assert fips("virginia", "Fairfax") == "51059"          # bare name is the county
    assert fips("new-york", "Kings") == "36047"            # borough
    assert fips("new-york", "Richmond") == "36085"
    assert fips("virginia", "Richmond") == "51159"         # same word, other state
    assert fips("missouri", "St. Louis") == "29189"
    assert fips("georgia", "Appling") == "13001"           # exact names unaffected
    assert fips("georgia", "Nowhere") == ""
    assert nbc_api._fips_by_name("maine", "townships") == {}


def test_every_fips_alias_points_at_a_real_table_entry():
    for state, aliases in nbc_api._FIPS_ALIASES.items():
        for target in aliases.values():
            assert target in nbc_api.FIPS_TABLE[state], (state, target)
