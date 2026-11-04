"""Fetch results from NBC News into raw_data[_<race>].csv.

    python fetch_results.py --race president|senate|governor|house

One HTTP call per state against NBC's results API (see nbc_api.py) for
president/senate/governor. House is one HTTP call total: NBC's national House
payload is already broken down by district, so there's no per-state loop for
it (see nbc_api.house_results()).

This file writes real vote counts only - no extrapolation. Projecting each
area's current vote share to 100% reporting (flat, or the historical-swing
model for areas with a baseline) happens client-side in estimate.js, per the
decision that the server fetches/caches raw data and never computes derived
values. See map.html for the projection and its per-STATE (or, for House,
per-DISTRICT) leader-mismatch check, which is why it aggregates every area of
a state rather than treating each one independently.

Each row also carries the race's strongest candidate who is neither Democrat
nor Republican (the three Independent columns); see nbc_api._independent_of.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

import nbc_api
from races import CSV_HEADER, HOUSE_DISTRICTS, RACES, race_files

WORKERS = 8  # parallel NBC state requests; keep <= the HTTP pool size (10)
EXIT_PARTIAL = 3  # some states failed; the file was still written


def _independent_cells(independent, votes):
    """The three appended columns: the race's third candidate, or zero/blank."""
    if not independent:
        return [0, "", ""]
    return [votes, independent["name"], independent["party"]]


def state_rows(payload):
    """One CSV row per reporting area of a state."""
    dem_name = payload["candidates"].get("dem", "")
    rep_name = payload["candidates"].get("gop", "")
    independent = payload.get("independent")

    rows = []
    for area in payload["areas"]:
        percent_in = area["percent_in"]
        if percent_in <= 0:
            # Nothing counted yet, so there is no share for estimate.js to
            # project forward. Absent areas already render as "no data".
            continue
        real = [area["by_party"].get("dem", 0), area["by_party"].get("gop", 0)]
        rows.append(
            [payload["state"], area["name"], area["fips"], payload["geography"],
             payload["total_expected"], area["votes"], percent_in]
            + real
            + [dem_name, rep_name]
            + _independent_cells(independent, area.get("independent_votes", 0))
        )
    return rows


def house_rows(districts):
    """One CSV row per House district - no per-state or per-county loop.

    Each row already *is* a full race (see nbc_api.house_results), unlike the
    other three races where a row is one county and the map sums counties up
    into a state. "State Total Expected" here is just that district's own
    expected total, which is also why the map's per-state aggregation is a
    no-op for House: grouping by district finds exactly one row.
    """
    rows = []
    for district in districts:
        percent_in = district["percent_in"]
        if percent_in <= 0:
            continue
        info = HOUSE_DISTRICTS.get(district["geoid"])
        if info is None:
            # DC and Puerto Rico have a non-voting delegate race NBC may list
            # here; they're not in HOUSE_DISTRICTS (no voting seat), so skip
            # rather than paint a seat that doesn't exist.
            continue
        real = [district["by_party"].get("dem", 0), district["by_party"].get("gop", 0)]
        rows.append(
            [info["state"], info["label"], district["geoid"], "districts",
             district["total_expected"], district["votes"], percent_in]
            + real
            + [district["candidates"].get("dem", ""), district["candidates"].get("gop", "")]
            + _independent_cells(district.get("independent"), district.get("independent_votes", 0))
        )
    return rows


def _or_exit_on_404(fn, race, cycle, *args):
    try:
        return fn(*args)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            # Routine before an election: the cycle only exists once there are
            # results. Say so in one line rather than a traceback - a scheduler
            # will hit this on every run for weeks beforehand.
            sys.exit(f"No {race} results published for {cycle} yet (404). "
                     f"Nothing to fetch; existing data is left alone.")
        raise


def fetch_race(race, cycle, skip):
    if race == "house":
        # One national payload, not one request per state - see
        # nbc_api.house_results(). --skip has no meaning here (there is no
        # per-state fetch to skip) and is silently ignored.
        districts, last_modified = _or_exit_on_404(nbc_api.house_results, race, cycle, cycle)
        return house_rows(districts), [], last_modified, len(districts)

    race_slug = RACES[race]["nbc_slug"]
    published = _or_exit_on_404(nbc_api.state_slugs, race, cycle, race_slug, cycle)

    slugs = [s for s in published if s not in skip]
    if not slugs:
        sys.exit(f"No states listed for {race} {cycle}. Has that cycle been published yet?")

    def one(state_slug):
        try:
            return state_slug, nbc_api.state_results(state_slug, race_slug, cycle), None
        except (requests.RequestException, ValueError, KeyError) as error:
            return state_slug, None, f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(one, slugs))

    rows, failures, no_county = [], [], []
    last_modified = None
    units = 0  # reporting areas NBC listed, counted or not
    for state_slug, payload, error in results:
        if error:
            failures.append((state_slug, error))
            continue
        if payload is None:
            continue  # no race of this type in this state - routine
        rows.extend(state_rows(payload))
        units += len(payload["areas"])
        lm = payload.get("last_modified")
        if lm and (last_modified is None or lm > last_modified):
            last_modified = lm  # ISO-8601 Z strings sort chronologically
        if not payload["county_level"]:
            no_county.append(f"{payload['state']} ({payload['geography']})")

    if no_county:
        # Not an error - these states genuinely have no county-level numbers,
        # and the map says so rather than inventing a county breakdown.
        print(f"Reported below county level, so no county map: {', '.join(sorted(no_county))}")
    return rows, failures, last_modified, units


def _state_slug(state_name):
    return state_name.lower().replace(" ", "-")  # NBC's URL slug for a state


# What a column holds for a row that was written before the column existed.
_APPENDED_DEFAULTS = {"Independent Real": "0"}


def _padded(row):
    """A row from an older CSV, widened to today's header. Without this a state
    that fails on the first refresh after a schema change would be written
    ragged: valid to the server's checks, misaligned under the new header."""
    return row + [_APPENDED_DEFAULTS.get(name, "") for name in CSV_HEADER[len(row):]]


def carried_forward_rows(previous_csv, failed_slugs, cycle):
    """The failed states' rows from the currently served CSV, if it is live
    data from the same cycle - never mock fixtures or another year's count."""
    try:
        meta = json.loads(previous_csv.with_suffix(".meta.json").read_text(encoding="utf-8"))
        if meta.get("source") != "live" or str(meta.get("dataYear")) != str(cycle):
            return []
        with open(previous_csv, newline="", encoding="utf-8") as file:
            reader = csv.reader(file, delimiter=";")
            next(reader, None)
            kept = [_padded(row) for row in reader if row and _state_slug(row[0]) in failed_slugs]
    except (OSError, ValueError):
        return []
    if kept:
        print(f"Kept previous rows for {', '.join(sorted({row[0] for row in kept}))}")
    return kept


def has_live_rows(previous_csv, cycle):
    """Whether the currently served CSV already holds real counts for this cycle."""
    try:
        meta = json.loads(previous_csv.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return meta.get("source") == "live" and str(meta.get("dataYear")) == str(cycle) and meta.get("rows", 0) > 0


def write_meta(output_csv, race, cycle, rows, source, last_modified=None):
    """A sidecar the page reads so it can say what it is showing, and when.

    Without this the page has no way to tell real results from the mock
    fixtures, or to show how stale the numbers are - both of which matter a
    lot more once this is deployed somewhere strangers can find it.
    """
    config = RACES[race]
    meta = {
        "race": race,
        "label": config["label"],
        "electionYear": config["election_year"],
        "dataYear": cycle,
        "source": source,
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourceLastModified": last_modified,
        "rows": len(rows),
        "states": len({row[0] for row in rows}),
    }
    meta_path = output_csv.with_suffix(".meta.json")
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, meta_path)


def write_csv(rows, output_csv):
    # Write-then-rename, so a crash mid-write can't leave a half-finished CSV
    # that the map would happily render as real results.
    tmp = output_csv.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    os.replace(tmp, output_csv)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race", default="president", choices=RACES.keys())
    parser.add_argument("--nbc-cycle", dest="nbc_cycle",
                        help="Year in NBC's URL path (default: the race's nbc_cycle in races.py)")
    parser.add_argument("--out-dir", default=".", help="Where to write the CSV (default: current directory)")
    parser.add_argument("--skip", default="", help="Comma-separated state slugs to skip, e.g. alaska,hawaii")
    parser.add_argument("--previous", type=Path,
                        help="Currently served CSV: a failed state keeps its rows from it, if it holds live data")
    args = parser.parse_args()

    race = RACES[args.race]
    cycle = args.nbc_cycle or race["nbc_cycle"]
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    output_csv = Path(args.out_dir) / race_files(args.race)["output_csv"].name

    print(f"{race['label']} {race['election_year']} results, from NBC's {cycle} pages")
    rows, failures, last_modified, units = fetch_race(args.race, cycle, skip)
    if failures and args.previous:
        rows += carried_forward_rows(args.previous, {slug for slug, _ in failures}, cycle)

    if not rows:
        # Every area at 0% is what NBC publishes before polls close: its pages
        # exist, nothing is counted. Say so with an empty live file instead of
        # leaving whatever was served before (demo figures, last cycle's
        # numbers) up as if it were this election. Anything else with no rows
        # is a failed or changed fetch, and a count only ever grows, so an
        # empty result after real rows is a glitch and must not replace them.
        if failures or not units or (args.previous and has_live_rows(args.previous, cycle)):
            sys.exit(f"No results parsed - refusing to overwrite {output_csv}.")
        print(f"All {units} reporting areas are at 0% - writing an empty live file")

    write_csv(rows, output_csv)
    write_meta(output_csv, args.race, cycle, rows, source="live", last_modified=last_modified)
    states = len({row[0] for row in rows})
    print(f"Wrote {len(rows)} rows across {states} states to {output_csv}")

    if failures:
        # The good states are already written; a distinct exit code lets a
        # scheduler serve this partial file while still flagging the failure.
        print(f"Failed: {', '.join(f'{slug} ({error})' for slug, error in failures)}")
        sys.exit(EXIT_PARTIAL)


if __name__ == "__main__":
    main()
