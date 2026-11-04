# Tests

Two kinds of test live here. The Selenium files drive a real headless Chrome,
because the bugs they exist to catch are layout bugs — something is the wrong
size, or in the wrong place, at some viewport. None of them are visible in the
markup. The rest are plain unit tests with no browser and no network.

```
pip install -r requirements.txt -r tests/requirements.txt
python -m pytest tests/               # everything, ~10 minutes
node --test tests/test_estimate.js    # projection math
```

Fast subset (no browser, a few seconds):

```
python -m pytest tests/test_nbc_api.py tests/test_historical_baseline.py \
  tests/test_generate_mock_data.py \
  tests/test_server_district_cache.py tests/test_partial_refresh.py
```

The browser suite builds a throwaway site in a temp directory from `map.html`
plus freshly generated mock CSVs and serves it, so it is deterministic and
needs no network beyond the CDN assets `map.html` itself loads. Most browser
tests run across the 7 viewports; only a few (`test_layout.py`'s render check
and `test_ui_independent.py`) also cover both themes × both palettes, each case
launching a browser.

Each test is a regression for something that actually shipped broken:

| File | Catches |
|---|---|
| `test_tooltip.py` | the tooltip leaving the viewport — it flipped away from the right edge but nothing clamped it, so on a narrow screen it overflowed the left instead. Includes an exhaustive unit test of the placement maths, which reaches corners the UI never produces. |
| `test_layout.py` | horizontal overflow, the map cropped or distorted, sub-44px tap targets, a page that scrolls despite being sized to fit, and a clean render in all four theme/palette combinations. |
| `test_overlay.py` | a drill-down with no reachable backdrop (on a phone the sheet filled the screen, leaving the close button as the only exit), focus not returning on Escape, a modal that isn't announced as one, and the page scrolling behind an open sheet. |
| `test_house.py` | House grouped by state instead of district, and the lazy district drill-down firing when it shouldn't or hanging when its fetch fails (against a stubbed `/house-district/<geoid>`), and districts redrawn for 2026 showing 2024 results from their old lines. |
| `test_map_helpers.py` | the geography label, summary tail and per-state county lookup that `map.html` shares across several call sites. |
| `test_explain.py` | the **?** breakdown drifting from the number it explains (every row's and the total's working must end at the figures on screen), Escape/focus on the popup, and the extra column overflowing a phone. Uses the real baselines, since the shared site's are empty. |
| `test_ballot_estimate.py` | a state's expected total ballots ignoring how this cycle's turnout is running (the 4-county worked example: 460k/400k -> 480k for President and for Senate/Governor), and the things that must not move: D/R projections, states with no baseline, finished states, township states, House districts. Feeds `buildRaceData` synthetic rows, no network. |
| `test_url_state.py` | the tab and drill-down not surviving F5 or a shared link; a dismissed drill-down reopening on refresh (every close route must clear it from the URL); malformed or unknown hashes erroring instead of falling back to the default view; the URL adding history entries; and pasting a new link into an open tab doing nothing. |
| `test_independents.py` *(no browser)* | the third candidate in the backend: picking the strongest non-Democrat, non-Republican candidate (not whoever NBC labels independent, not a sum by party code, never a write-in line), the same person in every county, the House feed's many party codes, the CSV only ever growing at the right-hand end (the first eleven columns never move), old rows padded when carried forward, and the mock data exercising every scenario. |
| `test_independent_model.py` | the third candidate in the data model, through `buildRaceData` with synthetic rows: a race without a contender is exactly what it was (including an older CSV with no independent columns), the 5%-and-1,000-vote line on both sides, one decision per race so a state's counties never mix models, flat three-way projection, a total that includes the contender, and plurality calls. |
| `test_ui_independent.py` | what a person sees: purple fills (standard and colour-blind textures), tooltips and sentences, the third drill-down row and columns, no row of zeros for a party with nobody running, the scoreboard and legend appearing only when the race has a contender, nothing overflowing at any viewport, a candidate name that tries to inject HTML, and the House district drill-down (including an older payload without the new fields). |
| `test_palette.py` | colour separation, measured with `scripts/check_palette.py` (CIEDE2000 validated against published pairs, Machado colour-blindness simulations): the existing red/blue fills did not move, and purple stays at least as far from every other fill as the measured floors in all four theme/palette combinations. |
| `test_nbc_api.py` *(no browser)* | `nbc_api.py`'s payload normalisation for state and district results. |
| `test_historical_baseline.py` *(no browser)* | `scripts/build_historical_baseline.py`'s join and aggregation logic. |
| `test_generate_mock_data.py` *(no browser)* | the mock CSV's header, delimiter and row shape. |
| `test_server_district_cache.py` *(no browser)* | `server.py`'s district cache growing past the 435 real districts, or concurrent misses each calling NBC. |
| `test_partial_refresh.py` *(no browser)* | one failed state freezing a whole race's refresh: failed states must carry forward from the served live CSV (never mock data) and the server must publish but report `degraded`; plus NBC's one-shot retry. |
| `test_estimate.js` *(Node, no browser)* | `estimate.js`'s flat and historical-swing projection maths, the turnout scaling, and the third-candidate rules: `leaderOf` and `callOf` checked against a verbatim copy of the old two-way rules over tens of thousands of random inputs, and the plurality lock proved to be the old majority rule for two candidates. |

Adding a viewport to `VIEWPORTS` in `conftest.py` extends every parametrised
test to it. `landscape` is excluded from the fit assertions on purpose: a
844×390 viewport cannot hold a 1.6:1 map plus chrome, and pretending otherwise
would mean asserting something false.
