"""Client for NBC News' election-results JSON API.

The results pages are a Next.js app that fetches this API itself; the endpoint
is unauthenticated and needs no cookies or query parameters. Going straight to
it instead of driving a browser is ~25x faster and, more importantly, it hands
us structured party codes and county FIPS that the rendered HTML never exposed.

    /firecracker/api/v2/national-results/{cycle}-elections/{race}-results
    /firecracker/api/v2/state-results/{cycle}-elections/{state}-{race}-results

`cycle` is the year in NBC's own URL path, which is the year of the election
being reported - see races.py, where it is kept separate from the election we
are notionally predicting.

GEOGRAPHY: NBC does not report every state by county. 43 states report by
county or parish; Connecticut, Maine, Massachusetts, New Hampshire, Vermont
report by township, Rhode Island by municipality, Alaska by legislative
district and DC by ward. Those eight have no county-level numbers at all, so
the honest thing is to say so rather than paint a county with one town's
votes. `payload["geography"]` tells us which case we are in.

HOUSE IS DIFFERENT: the national-results payload for "house" already breaks
the country down by district (`mapData`, keyed by district GEOID) rather than
by state, so `house_results()` needs none of `state_results()`'s per-state
fetch loop - see that function for why.
"""

import json
import re
import unicodedata
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from races import ELECTORAL_VOTES, HOUSE_DISTRICTS as _HOUSE_DISTRICTS, SENATE_SPECIAL_STATES_2026

BASE_URL = "https://www.nbcnews.com/firecracker/api/v2"
# NBC's edge is happy with a plain client; identify honestly rather than
# impersonating a browser.
USER_AGENT = "usa-election-predictor/1.0 (+https://github.com/romainhedouin/USA-election-predictor)"
TIMEOUT = (5, 30)  # (connect, read) seconds

# Shared so repeated NBC requests reuse one keep-alive connection pool.
_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = USER_AGENT
# One retry for connect errors and transient 5xx/429s. Read timeouts are not
# retried, and Retry-After is ignored, so a slow NBC can't multiply the
# per-request time past server.py's FETCH_TIMEOUT.
_SESSION.mount("https://", HTTPAdapter(max_retries=Retry(
    total=1, connect=1, read=0, status=1, backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=False, raise_on_status=False,
)))

# Geographies whose reporting units are actual counties, and therefore line up
# with the county boundaries the map draws.
COUNTY_GEOGRAPHIES = {"counties", "parishes"}

# (state slug) -> {NBC area name: 5-digit FIPS}. Built once by
# build_fips_table.py; see that script for why this is a table and not a
# name match done at runtime.
FIPS_TABLE = json.loads((Path(__file__).parent / "static" / "county_fips.json").read_text(encoding="utf-8"))

# NBC's 2026 pages spell some areas differently from the 2024 pages the table
# above was frozen from ("De Witt" / "DeWitt", "Doña Ana" / "Dona Ana"), and
# use bare county names where 2024 said "<Name> County" or a borough. Spelling
# differences are absorbed by _norm(); these are the ones that are a different
# name altogether. Values are keys of FIPS_TABLE[state].
_FIPS_ALIASES = {
    "arkansas": {"Arkansas": "Arkansas County"},
    "idaho": {"Idaho": "Idaho County"},
    "oklahoma": {"Oklahoma": "Oklahoma County"},
    "utah": {"Utah": "Utah County"},
    "maryland": {"Baltimore": "Baltimore County"},
    "missouri": {"St. Louis": "St. Louis County"},
    "nevada": {"Carson": "Carson City"},
    "new-york": {"Kings": "Brooklyn", "New York": "Manhattan", "Richmond": "Staten Island"},
    "virginia": {"Fairfax": "Fairfax County", "Franklin": "Franklin County", "Richmond": "Richmond County",
                 "Roanoke": "Roanoke County"},
}

# Slugs of the places that can have a statewide race page. The national index
# also links other offices (lieutenant governor, or governor from the Senate
# page) whose slugs are not a bare state.
_STATE_SLUGS = {state.lower().replace(" ", "-") for state in ELECTORAL_VOTES}
_SPECIAL_SENATE_SLUGS = {state.lower().replace(" ", "-") for state in SENATE_SPECIAL_STATES_2026}


def _norm(name):
    """Case, spacing, punctuation and accents ignored: "De Witt" == "DeWitt"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


def _fips_by_name(state_slug, geography):
    """NBC area name -> FIPS for one state, or {} where areas aren't counties.

    Besides the table's own names it carries each name's _norm() form under a
    "~" prefix and this cycle's aliases, which _build_areas falls back to.
    """
    if geography not in COUNTY_GEOGRAPHIES:
        return {}
    table = FIPS_TABLE.get(state_slug, {})
    lookup = dict(table)
    lookup.update({"~" + _norm(name): fips for name, fips in table.items()})
    for alias, name in _FIPS_ALIASES.get(state_slug, {}).items():
        lookup[alias] = table[name]
    return lookup


# The Census GEOID standard - what the district topology and house_districts
# table both use - numbers an at-large state's lone district "00". NBC's own
# API instead calls it "District 1" and keys it "...01" (checked live for all
# six current at-large states: AK, DE, MT is NOT at-large post-2020 so it's
# unaffected, ND, SD, VT, WY). Left alone, that mismatch would silently fail
# to join those six states to the map - so rewrite "01" back to the standard
# "00" for any state that this cycle's topology says only has one district.
_AT_LARGE_STATE_FIPS = {geoid[:2] for geoid in _HOUSE_DISTRICTS if geoid.endswith("00")}


def _fix_at_large_geoid(geoid):
    return geoid[:2] + "00" if geoid[:2] in _AT_LARGE_STATE_FIPS else geoid


def _get(url):
    response = _SESSION.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _get_or_none(url):
    """_get(), but None on a 404 (NBC has no such page)."""
    try:
        return _get(url)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return None
        raise


def state_slugs(race_slug, cycle):
    """Every state slug NBC publishes for this race, in the order it lists them."""
    payload = _get(f"{BASE_URL}/national-results/{cycle}-elections/{race_slug}-results")

    # The state list is nested a few levels down inside the page payload and
    # NBC has moved it before, so walk for the shape rather than a fixed path.
    found = []

    def walk(node):
        if isinstance(node, dict):
            # Before polls open NBC lists some states (Delaware's Senate seat
            # on 3 Nov 2026) with a null href: no page to fetch yet. It also
            # links lieutenant-governor pages from the governor index and
            # governor pages from the Senate one, which are not this race.
            if "stateName" in node and node.get("href"):
                slug = node["href"].rsplit("/", 1)[-1].removesuffix(f"-{race_slug}-results")
                if slug in _STATE_SLUGS:
                    found.append(slug)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return list(dict.fromkeys(found))  # NBC repeats states across page sections


def state_results(state_slug, race_slug, cycle):
    """One state's results, normalised.

    Returns None if NBC has no race of this type in this state (a 404), which
    is routine: most states have no Senate or governor race in a given cycle.
    """
    special = race_slug == "senate" and state_slug in _SPECIAL_SENATE_SLUGS
    url = f"{BASE_URL}/state-results/{cycle}-{'special-' if special else ''}elections/{state_slug}-{race_slug}-results"
    payload = _get_or_none(url)
    races = payload and payload.get("races")
    if not races:
        return None
    # Maine and Nebraska split their electoral votes, so NBC publishes a second
    # race holding just the congressional districts. The state-wide one is the
    # one carrying every reporting unit.
    race = max(races, key=lambda r: len(r.get("areas") or []))

    geography = payload.get("geography")
    fips_by_name = _fips_by_name(state_slug, geography)

    return {
        "state": payload.get("stateName") or state_slug,
        "geography": geography,
        "county_level": geography in COUNTY_GEOGRAPHIES,
        **_summary_fields(payload, race, fips_by_name),
    }


def district_results(geoid, cycle):
    """One House district's own county-level (or equivalent) breakdown.

    Fetched only on demand - see server.py's /house-district/<geoid> route -
    never as part of the regular refresh cycle, which only needs
    house_results()'s single national call. Returns None if NBC has no page
    for this district (a 404, or a GEOID we don't recognise at all).

    Same area shape as state_results(), for the same reason: county rows in,
    Real/Predicted extrapolation applied the same way by the caller.
    """
    info = _HOUSE_DISTRICTS.get(geoid)
    if info is None:
        return None
    state_slug = info["state"].lower().replace(" ", "-")
    # NBC numbers an at-large state's lone district "1" in these URLs, not
    # the Census GEOID's "00" - the same quirk _fix_at_large_geoid corrects
    # the other direction for the national payload.
    district_num = 1 if geoid[:2] in _AT_LARGE_STATE_FIPS else int(geoid[2:])

    url = f"{BASE_URL}/state-results/{cycle}-elections/{state_slug}-us-house-district-{district_num}-results"
    payload = _get_or_none(url)
    races = payload and payload.get("races")
    if not races:
        return None
    race = races[0]

    # The office's own geography here is "districts" (payload["geography"] -
    # see state_results for the statewide equivalent of that field); what
    # matters for joining areas to county shapes is the breakdown granularity
    # underneath, which NBC calls underlyingGeographies on this endpoint.
    geography = payload.get("underlyingGeographies")
    fips_by_name = _fips_by_name(state_slug, geography)

    return {
        "geoid": geoid,
        "label": info["label"],
        "state": info["state"],
        "geography": geography,
        "county_level": geography in COUNTY_GEOGRAPHIES,
        **_summary_fields(payload, race, fips_by_name),
    }


def _summary_fields(payload, race, fips_by_name):
    """The five fields state_results() and district_results() both derive
    the same way from a race's summary/areas - factored out so the two stay
    in lockstep rather than drifting if one is edited and not the other."""
    summary = race.get("summary") or {}
    # Fixed once for the whole race, from the race summary, so every county
    # reports the votes of the same person - see _independent_of.
    independent = _independent_of(summary.get("candidates") or [])
    return {
        "total_expected": int(summary.get("votes") or 0) + int((summary.get("estimatedVotesRemaining") or {}).get("value") or 0),
        "percent_in": float(summary.get("percentIn") or 0),
        "last_modified": payload.get("lastModified"),
        "candidates": _leading_by_party(summary.get("candidates") or []),
        "independent": independent,
        "areas": _build_areas(race.get("areas") or [], fips_by_name, independent),
    }


def _build_areas(areas_raw, fips_by_name, independent=None):
    return [
        {
            "name": area["name"],
            "fips": fips_by_name.get(area["name"]) or fips_by_name.get("~" + _norm(area["name"]), ""),
            "percent_in": float(area.get("percentIn") or 0),
            "votes": int(area.get("votes") or 0),
            "by_party": _votes_by_party(area.get("candidates") or []),
            "independent_votes": _votes_for(area.get("candidates") or [], independent),
        }
        for area in areas_raw
    ]


def house_results(cycle):
    """Every U.S. House district's results, normalised, in one call.

    Unlike president/senate/governor, NBC already breaks the House down to
    one race per district in a single national payload
    (`mapData`, keyed by the standard 4-digit district GEOID - state FIPS +
    2-digit district number, "00" for an at-large seat) - so there is no
    per-state fetch loop here the way there is in `state_results()`.
    """
    payload = _get(f"{BASE_URL}/national-results/{cycle}-elections/house-results")
    map_data = payload.get("mapData") or {}

    districts = []
    for geoid, entry in map_data.items():
        tooltip = entry.get("tooltip") or {}
        candidates = tooltip.get("candidates") or []
        votes = int(tooltip.get("totalVote") or 0)
        # Chosen per district: each is its own race, and the House feed has no
        # candidate ids to match on, so names do.
        independent = _independent_of(candidates)
        districts.append({
            "geoid": _fix_at_large_geoid(geoid),
            "percent_in": float(tooltip.get("percentIn") or 0),
            "votes": votes,
            "total_expected": votes + int(tooltip.get("remainingVote") or 0),
            "by_party": _votes_by_party(candidates),
            "candidates": _leading_by_party(candidates),
            "independent": independent,
            "independent_votes": _votes_for(candidates, independent),
        })
    return districts, payload.get("lastModified")


# The House's national payload tags the Republican candidate's party "Rep"
# (Title case); every other endpoint here already calls it "gop" (see
# state_results() above). Normalise both to lower case so downstream code
# only ever has to check one spelling.
_PARTY_ALIASES = {"rep": "gop"}


def _party_code(candidate):
    code = (candidate.get("party") or "").lower()
    return _PARTY_ALIASES.get(code, code)


def _votes_by_party(candidates):
    """Votes keyed by NBC's party code ("dem" / "gop" / "other" / ...)."""
    totals = {}
    for candidate in candidates:
        party = _party_code(candidate)
        totals[party] = totals.get(party, 0) + int(candidate.get("votes") or 0)
    return totals


def _leading_by_party(candidates):
    """The leading candidate's name per party, state-wide.

    Party comes from NBC rather than from candidate order: ranking by votes
    would label whoever happens to be ahead as the Democrat, and a same-party
    runoff would silently drop one side entirely.
    """
    best = {}
    for candidate in candidates:
        party = _party_code(candidate)
        votes = int(candidate.get("votes") or 0)
        if party not in best or votes > best[party][1]:
            best[party] = (candidate.get("name", ""), votes)
    return {party: name for party, (name, _) in best.items()}


# ---- The third candidate -------------------------------------------------
#
# Every race the map draws is Democrat against Republican, but NBC lists
# everyone, and some third candidates are real contenders (Nebraska's Dan
# Osborn held 43.7% of a 715,000-vote count). One per race is carried through:
# the strongest candidate who is neither Democrat nor Republican.
#
# This is deliberately NOT "whoever NBC labels independent". NBC's labels are
# not reliable for that: Osborn is coded "other", Kevin Kiley "NPP" in the
# House feed but "other" in the district feed, and the House feed alone uses
# 26 different party codes. Summing by code is no better - Nebraska's "other"
# adds Osborn to three candidates under 3% each and would read as a 49.7% bloc.

_MAJOR_PARTIES = frozenset({"dem", "gop"})

# The party codes that say something on their own. Everything else - NBC's
# "other"/"Oth" catch-all and a long tail of one-off state parties - is left
# unlabelled rather than guessed at, so the page shows just the name.
_PARTY_LABELS = {
    "ind": "Independent",
    "lib": "Libertarian",
    "grn": "Green",
    "npa": "No party affiliation",
    "np": "Nonpartisan",
    "npp": "No party preference",
    "una": "Unaffiliated",
    "nol": "No Labels",
}


def _name_key(name):
    return " ".join((name or "").split()).casefold()


def _is_write_in(name):
    """NBC lists write-ins as lines of their own: "Total Write-ins" (the House
    feed even pads it with a leading space), "Jane Doe (write-in)", and in
    Nevada "None of These Candidates". None of them is a candidate to draw."""
    lowered = (name or "").strip().lower()
    return "write-in" in lowered or "write in" in lowered or "none of these" in lowered


def _independent_of(candidates):
    """The strongest non-Democrat, non-Republican candidate with any votes, as
    {"name", "party", "code"}, or None. Ties go to NBC's own listing order,
    then the name, so two refreshes of the same count agree."""
    contenders = [
        candidate for candidate in candidates
        if _party_code(candidate) not in _MAJOR_PARTIES
        and not _is_write_in(candidate.get("name"))
        and int(candidate.get("votes") or 0) > 0
    ]
    if not contenders:
        return None

    def rank(candidate):
        order = candidate.get("order")
        return (-int(candidate.get("votes") or 0), order if isinstance(order, int) else 10**6, candidate.get("name") or "")

    best = min(contenders, key=rank)
    return {
        "name": (best.get("name") or "").strip(),
        "party": _PARTY_LABELS.get(_party_code(best), ""),
        "code": best.get("code"),
    }


def _votes_for(candidates, independent):
    """What the chosen candidate got in one area: matched on NBC's candidate
    code where both sides have one, else on the name. 0 if not listed."""
    if not independent:
        return 0
    code, name = independent.get("code"), _name_key(independent.get("name"))
    total = 0
    for candidate in candidates:
        if code is not None and candidate.get("code") is not None:
            same = candidate["code"] == code
        else:
            same = _name_key(candidate.get("name")) == name
        if same:
            total += int(candidate.get("votes") or 0)
    return total
