"""A third candidate on screen: fills, scoreboard, tooltips, drill-down, legend.

test_independent_model.py checks the numbers; this checks what a person sees.
The mock site (generate_mock_data.py) has, with empty baselines:

  Governor Wyoming   an independent leads statewide           -> purple, projected
  House 1601         an independent leads, nearly all counted -> purple, certain
  Senate Idaho       like Nebraska: no Democrat, R vs I       -> red, certain, two rows not three
  Senate Wyoming,
  Governor Vermont   an independent under 5%                   -> unchanged, nothing purple

and the President tab has no independent at all, so every check has a "without" to
compare against. A custom site (see custom_site) adds the cases the mock cannot:
a purple FAINT fill and a candidate name that tries to inject HTML.
"""

import csv
import json
import shutil
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from conftest import (STUB_CA01, VIEWPORTS, click_district, house_pages, make_driver, severe_logs,
                      wait_for_note, wait_ready)

OPEN_TAB = """
[...document.querySelectorAll('#race-tabs button')]
  .find(b => b.firstElementChild.textContent === arguments[0]).click();"""
FILL = """
const p = [...document.querySelectorAll('#map path.state')]
  .find(p => p.getAttribute('aria-label').startsWith(arguments[0] + ':'));
return p.getAttribute('fill');"""
TOKEN = "return getComputedStyle(document.documentElement).getPropertyValue(arguments[0]).trim();"
HOVER = """
const p = [...document.querySelectorAll('#map path.state')]
  .find(p => p.getAttribute('aria-label').startsWith(arguments[0] + ':'));
const r = p.getBoundingClientRect();
p.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}));
return document.querySelector('#tooltip').innerText;"""
FILL_DISTRICT = """
const p = [...document.querySelectorAll('#map path.state')].find(p => p.__data__ && p.__data__.id === arguments[0]);
return p.getAttribute('fill');"""
OPEN_STATE_NAMED = """
const p = [...document.querySelectorAll('#map path.state')]
  .find(p => p.getAttribute('aria-label').startsWith(arguments[0] + ':'));
p.dispatchEvent(new MouseEvent('click', {bubbles: true}));"""
IS_OPEN = "return document.querySelector('#overlay').classList.contains('open');"
CAND_ROWS = ("return [...document.querySelectorAll('#overlay-summary .cand-row')]"
             ".map(r => r.querySelector('.cand-name').innerText.trim());")
# textContent, not innerText (CSS upper-cases that); the party headers hold a full name and a one-letter phone form.
TABLE_HEAD = ("return [...document.querySelectorAll('#overlay-table thead th')]"
              ".map(t => (t.querySelector('.th-full') || t).textContent.trim());")
ACTIVE_TAB = ("return document.querySelector('#race-tabs button[aria-pressed=\"true\"]')"
              ".firstElementChild.textContent;")


def open_tab(driver, label):
    driver.execute_script(OPEN_TAB, label)
    WebDriverWait(driver, 25).until(
        lambda d: d.execute_script(ACTIVE_TAB) == label
        and d.execute_script("return document.querySelectorAll('#map path.state').length > 0"))
    time.sleep(0.15)


def token(driver, name):
    return driver.execute_script(TOKEN, name)


def tooltip(driver, name):
    return driver.execute_script(HOVER, name)


# ============================================================ fills

def test_purple_projected_in_the_standard_palette(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    assert driver.execute_script(FILL, "Wyoming") == token(driver, "--ind-projected")
    assert not severe_logs(driver)


def test_purple_certain_on_the_house_map(page):
    driver = page("desktop")
    open_tab(driver, "House")
    assert driver.execute_script(FILL_DISTRICT, "1601") == token(driver, "--ind-certain")


def test_a_contender_who_is_second_changes_nothing_about_the_leaders_fill(page):
    """Nebraska-like: Republican certain, an independent second - the map is still red."""
    driver = page("laptop")
    open_tab(driver, "Senate")
    assert driver.execute_script(FILL, "Idaho") == token(driver, "--rep-certain")


@pytest.mark.parametrize("race, state, expected", [
    ("Senate", "Wyoming", "--rep-projected"),     # an independent at 3%: ignored, so exactly the old fill
    ("Governor", "Vermont", "--dem-certain"),     # an independent at 2%
])
def test_an_independent_under_the_threshold_leaves_the_fill_alone(page, race, state, expected):
    driver = page("laptop")
    open_tab(driver, race)
    assert driver.execute_script(FILL, state) == token(driver, expected)


def test_colour_blind_mode_marks_an_independent_by_texture(page):
    driver = page("laptop", palette="cvd")
    open_tab(driver, "Governor")
    assert driver.execute_script(FILL, "Wyoming") == "url(#hatch-ind-nat)"
    open_tab(driver, "House")
    assert driver.execute_script(FILL_DISTRICT, "1601") == "url(#dots-ind-nat)"


def test_the_three_patterns_use_three_different_marks(page):
    driver = page("laptop", palette="cvd")
    open_tab(driver, "Governor")
    marks = driver.execute_script("""
      const t = id => { const p = document.getElementById(id + '-nat');
                        return p ? {transform: p.getAttribute('patternTransform'), hasDot: !!p.querySelector('circle'),
                                    hasLine: !!p.querySelector('line')} : null; };
      return {dem: t('hatch-dem'), rep: t('hatch-rep'), ind: t('hatch-ind'), indFaint: t('hatch-ind-faint'), dots: t('dots-ind')};""")
    assert marks["dem"]["transform"] == "rotate(45)" and marks["rep"]["transform"] == "rotate(-45)"
    # An independent's stripes are the one orientation the other two do not use.
    assert marks["ind"]["transform"] == "rotate(0)" and marks["indFaint"]["transform"] == "rotate(0)"
    assert marks["ind"]["hasLine"] and not marks["ind"]["hasDot"]
    assert marks["dots"]["hasDot"] and not marks["dots"]["hasLine"]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("palette", [None, "cvd"])
def test_every_combination_renders_a_contender_without_errors(page, theme, palette):
    driver = page("laptop", theme=theme, palette=palette)
    for tab in ("Governor", "Senate", "House"):
        open_tab(driver, tab)
    # No fill may reference a pattern that does not exist (it would paint nothing).
    missing = driver.execute_script("""
      return [...document.querySelectorAll('#map path.state')].map(p => p.getAttribute('fill'))
        .filter(f => f && f.startsWith('url(#') && !document.getElementById(f.slice(5, -1))).length;""")
    assert missing == 0
    assert not severe_logs(driver)


# ====================================================== text: tooltip, note

def test_the_tooltip_names_the_independent_and_the_leader(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    text = tooltip(driver, "Wyoming")
    assert "projected Independent" in text and "Independent (Independent)" not in text   # the mock's candidate is called "Independent"
    assert "holds 48.9% of the votes counted so far" in text


def test_a_two_candidate_race_reads_exactly_as_before(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    text = tooltip(driver, "Vermont")
    assert "holds" not in text and "Independent" not in text
    assert text.splitlines()[0] == "Vermont" and "certain Democrat" in text


def test_the_overlay_note_and_aria_label_carry_it_too(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    label = driver.execute_script(
        "return [...document.querySelectorAll('#map path.state')].find(p => p.getAttribute('aria-label').startsWith('Wyoming:')).getAttribute('aria-label');")
    assert "holds 48.9%" in label
    driver.execute_script(OPEN_STATE_NAMED, "Wyoming")
    assert "holds 48.9%" in driver.execute_script("return document.querySelector('#overlay-note').textContent;")


# ============================================================= drill-down

def test_the_drilldown_has_a_third_row_and_two_more_columns(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE_NAMED, "Wyoming")
    assert driver.execute_script(IS_OPEN)
    assert driver.execute_script(CAND_ROWS) == ["Democrat", "Republican", "Independent"]
    head = driver.execute_script(TABLE_HEAD)
    assert head[:2] == ["Area", "In"] and head[2:8] == ["Democrat", "Proj.", "Republican", "Proj.", "Independent", "Proj."]
    # a county where nothing is in yet spans every column
    colspan = driver.execute_script("""
      const td = [...document.querySelectorAll('#overlay-table td[colspan]')][0];
      return td ? +td.getAttribute('colspan') : null;""")
    assert colspan in (None, 8)


def test_without_a_contender_the_drilldown_is_unchanged(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE_NAMED, "Vermont")
    assert driver.execute_script(CAND_ROWS) == ["Democrat", "Republican"]
    assert len(driver.execute_script(TABLE_HEAD)) == 7          # Area, In, D, Proj, R, Proj, ?


def test_a_party_with_nobody_running_is_not_drawn_beside_a_contender(page):
    """Nebraska-like: there is no Democrat, so no row of 'Democrat 0 -> 0'."""
    driver = page("laptop")
    open_tab(driver, "Senate")
    driver.execute_script(OPEN_STATE_NAMED, "Idaho")
    assert driver.execute_script(CAND_ROWS) == ["Republican", "Independent"]
    # ... and no column of zeros for it in the table either.
    assert driver.execute_script(TABLE_HEAD) == ["Area", "In", "Republican", "Proj.", "Independent", "Proj.", "How it is computed"]
    wide = driver.execute_script(
        "const td = [...document.querySelectorAll('#overlay-table td[colspan]')][0]; return td ? +td.getAttribute('colspan') : null;")
    assert wide in (None, 6)


def test_county_tooltips_show_three_numbers_in_a_contender_race(page):
    driver = page("desktop")
    open_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE_NAMED, "Wyoming")
    text = driver.execute_script("""
      const county = document.querySelector('#state-map path.county');
      const r = county.getBoundingClientRect();
      county.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}));
      return document.querySelector('#tooltip').innerText;""")
    assert " D / " in text and " R / " in text and text.count(" I") >= 1


def test_the_explanations_end_at_the_figures_on_screen(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE_NAMED, "Wyoming")
    driver.execute_script("document.querySelector('button.why-total').click();")
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("return !!document.querySelector('#explain');"))
    text = driver.execute_script("return document.querySelector('#explain').innerText;")
    assert "A third candidate" in text and "plurality" not in text.lower()
    assert "once nobody else can catch up" in text
    # The projected independent total in the popup is the figure in the summary bar.
    import re
    shown = driver.execute_script(
        "const rows = document.querySelectorAll('#overlay-summary .cand-row'); return rows[rows.length - 1].querySelector('.cand-numbers').innerText;")
    projected = int(re.findall(r"[\d,]+", shown)[-1].replace(",", ""))
    assert f"I {projected:,}" in text.replace("\n", " ") or f"{projected:,}" in text


def test_a_county_explanation_has_an_I_line(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE_NAMED, "Wyoming")
    driver.execute_script("document.querySelector('#overlay-table tr.explainable button.why').click();")
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("return !!document.querySelector('#explain');"))
    text = driver.execute_script("return document.querySelector('#explain').innerText;")
    import re
    lines = {m[0]: int(m[1].replace(",", "")) for m in re.findall(r"^([DRI]) .*= ([\d,]+)$", text, re.M)}
    assert set(lines) == {"D", "R", "I"}
    # the line ends at the number in that county's row of the table
    row = driver.execute_script("return [...document.querySelector('#overlay-table tr.explainable').children].map(c => c.innerText);")
    assert str(f"{lines['I']:,}") in " ".join(row)


# ===================================================== scoreboard and legend

def test_the_scoreboard_counts_independent_seats_separately(page):
    driver = page("laptop")
    open_tab(driver, "Governor")
    sb = driver.execute_script("""
      const ind = document.querySelector('#scoreboard .ind-note');
      const widths = [...document.querySelectorAll('#scoreboard .bar > div')].map(d => parseFloat(d.style.width) || 0);
      return {ind: ind ? ind.innerText : null, sum: widths.reduce((a, b) => a + b, 0),
              sides: [...document.querySelectorAll('#scoreboard .side')].length};""")
    assert sb["ind"] and "Independent / other: 1 governorship" in sb["ind"] and "counted for neither party" in sb["ind"]
    assert sb["sum"] == pytest.approx(100, abs=0.01)
    assert sb["sides"] == 2          # still just Democrat and Republican blocks


def test_no_independent_block_when_no_seat_is_independent(page):
    driver = page("laptop")           # President tab: no independent anywhere
    assert driver.execute_script("return !!document.querySelector('#scoreboard .ind-note');") is False
    sum_ = driver.execute_script(
        "return [...document.querySelectorAll('#scoreboard .bar > div')].map(d => parseFloat(d.style.width) || 0).reduce((a, b) => a + b, 0);")
    assert sum_ == pytest.approx(100, abs=0.01)


def test_the_legend_and_hint_appear_only_when_something_is_drawn_purple(page):
    driver = page("laptop", palette="cvd")
    hidden = "return [document.querySelector('#legend-ind').hidden, document.querySelector('.hint-ind').hidden];"
    assert driver.execute_script(hidden) == [True, True]                 # President: no independent at all
    open_tab(driver, "Senate")
    # Idaho has a contender (Republican first, an independent second): nothing is purple, so no legend item.
    assert driver.execute_script(hidden) == [True, True]
    open_tab(driver, "Governor")                                         # Wyoming: an independent leads
    assert driver.execute_script(hidden) == [False, False]
    shown = driver.execute_script("return getComputedStyle(document.querySelector('#legend-ind')).display;")
    assert shown != "none"
    open_tab(driver, "President")
    assert driver.execute_script(hidden) == [True, True]


# ============================================================ layout

@pytest.mark.parametrize("viewport", [v for v in VIEWPORTS if v != "landscape"])
def test_the_taller_legend_still_fits_the_screen_on_a_contender_race(page, viewport):
    driver = page(viewport)
    open_tab(driver, "Governor")
    slack = driver.execute_script("""
      return document.querySelector('#legend').getBoundingClientRect().bottom - document.documentElement.clientHeight;""")
    assert slack <= 8, f"map+legend overflow the first screen by {slack:.0f}px with the independent legend showing"
    overflow = driver.execute_script("const e = document.documentElement; return e.scrollHeight - e.clientHeight;")
    assert overflow <= 0, f"page scrolls vertically by {overflow}px"


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_the_wider_table_and_scoreboard_do_not_overflow(page, viewport):
    driver = page(viewport)
    open_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE_NAMED, "Wyoming")
    over = driver.execute_script("""
      const panel = document.querySelector('#overlay-panel');
      const board = document.querySelector('#scoreboard');
      return {panel: panel.scrollWidth - panel.clientWidth,
              page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              board: board.scrollWidth - board.clientWidth};""")
    assert over["panel"] <= 0 and over["page"] <= 0 and over["board"] <= 0, over


# ============================================== a custom site for the rest

@pytest.fixture(scope="module")
def custom_site(site, tmp_path_factory):
    """The mock site plus governor rows the mock lacks, and a baseline that makes
    one of them 'unprojected' (a total far larger than what has been counted)."""
    root = tmp_path_factory.mktemp("ind-site")
    shutil.copytree(site, root, dirs_exist_ok=True)
    return root


def _write_governor(root, extra_rows, historical=None):
    path = root / "raw_data_governor.csv"
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file, delimiter=";"))
    header = rows[0]
    rows = [r for r in rows[1:] if r[0] not in {x[0] for x in extra_rows}] + extra_rows
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    (root / "static" / "historical_governor.json").write_text(json.dumps(historical or {}), encoding="utf-8")


def _serve(root):
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _row(state, county, fips, pct, total, d, r, i, name="Independent", expected=0):
    return [state, county, fips, "counties", expected or total, total, pct, d, r, "Democrat", "Republican", i, name, "Independent"]


@pytest.fixture
def custom_page(custom_site):
    drivers, servers = [], []

    def open_page(extra_rows, historical=None, viewport="laptop", palette=None, hash_=""):
        _write_governor(custom_site, extra_rows, historical)
        server, base = _serve(custom_site)
        servers.append(server)
        width, height, mobile = VIEWPORTS[viewport]
        driver = make_driver(width, height, mobile)
        drivers.append(driver)
        driver.get(f"{base}/map.html")
        driver.execute_script("localStorage.setItem('theme','light');" + (
            "localStorage.setItem('palette','cvd');" if palette else "localStorage.removeItem('palette');"))
        driver.get(f"{base}/map.html{hash_}")
        wait_ready(driver)
        return driver

    yield open_page
    for driver in drivers:
        driver.quit()
    for server in servers:
        server.shutdown()


OHIO_FAINT = [_row("Ohio", "Adams", "39001", 10, 5500, 1000, 1500, 3000, expected=60000)]
OHIO_BASELINE = {"Ohio": {"demShare": 0.4, "repShare": 0.6, "votes": 4000000, "year": 2022}}
TEXAS_CERTAIN = [_row("Texas", "Harris", "48201", 100, 200000, 20000, 30000, 150000)]
IOWA_DEM = [_row("Iowa", "Adair", "19001", 50, 50000, 25000, 15000, 10000)]


def test_a_purple_faint_fill_when_the_independent_leads_but_nothing_is_projected(custom_page):
    driver = custom_page(OHIO_FAINT, OHIO_BASELINE)
    open_tab(driver, "Governor")
    assert driver.execute_script(FILL, "Ohio") == token(driver, "--ind-faint")


def test_the_faint_purple_is_a_stripe_in_colour_blind_mode(custom_page):
    driver = custom_page(OHIO_FAINT, OHIO_BASELINE, palette="cvd")
    open_tab(driver, "Governor")
    assert driver.execute_script(FILL, "Ohio") == "url(#hatch-ind-faint-nat)"


def test_certain_purple_and_the_other_parties_fills_beside_it(custom_page):
    driver = custom_page(TEXAS_CERTAIN + IOWA_DEM)
    open_tab(driver, "Governor")
    assert driver.execute_script(FILL, "Texas") == token(driver, "--ind-certain")
    # Iowa: Democrat first with an independent at 20%: still a Democratic fill, projected.
    assert driver.execute_script(FILL, "Iowa") == token(driver, "--dem-projected")


def test_the_hash_deep_link_opens_a_contender_race(page, base_url):
    driver = page("laptop")
    driver.get(f"{base_url}/map.html#governor/56")
    driver.refresh()
    wait_ready(driver)
    WebDriverWait(driver, 25).until(lambda d: d.execute_script(IS_OPEN))
    assert driver.execute_script(CAND_ROWS) == ["Democrat", "Republican", "Independent"]


# ---------------------------------------------------------------- injection

EVIL = '<img src=x onerror="window.__xss=1">Evil'


def test_a_candidate_name_cannot_inject_html(custom_page):
    """Names come from NBC. They now reach tooltips, sentences, bars, headers and
    the working, all built as HTML, so every one has to be escaped."""
    rows = [_row("Wyoming", "Natrona", "56025", 50, 40000, 4800, 13200, 22000, name=EVIL),
            _row("Wyoming", "Laramie", "56021", 100, 55000, 8250, 20900, 25850, name=EVIL)]
    driver = custom_page(rows)
    open_tab(driver, "Governor")
    tooltip(driver, "Wyoming")
    driver.execute_script(OPEN_STATE_NAMED, "Wyoming")
    driver.execute_script("document.querySelector('button.why-total').click();")
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("return !!document.querySelector('#explain');"))
    time.sleep(0.6)
    assert driver.execute_script("return window.__xss;") is None, "a candidate name ran as HTML"
    assert driver.execute_script(
        "return document.querySelectorAll('#tooltip img, #overlay img, #explain img, #scoreboard img').length;") == 0
    # ... and the name is still shown, as text.
    assert "Evil" in driver.execute_script("return document.querySelector('#overlay-summary').innerText;")
    assert "<img" in driver.execute_script("return document.querySelector('#overlay-summary').innerText;")


# -------------------------------------------------------- House district

STUB_ID1 = {
    "geoid": "1601", "label": "Idaho District 1", "state": "Idaho", "geography": "counties", "countyLevel": True,
    "totalExpected": 320000, "percentIn": 97.0, "demName": "Democrat", "repName": "Republican",
    "indName": "Independent", "indParty": "Independent", "lastModified": None,
    "areas": [
        {"name": "Ada", "fips": "16001", "percentIn": 97.0, "votes": 213400, "demReal": 17000, "repReal": 85000, "indReal": 111000},
        {"name": "Canyon", "fips": "16027", "percentIn": 96.0, "votes": 96000, "demReal": 7800, "repReal": 39000, "indReal": 49200},
    ],
}


@pytest.fixture
def house_contender(site):
    yield from house_pages(site, {"1601": STUB_ID1, "0601": STUB_CA01}, delay=0.1, default_viewport="desktop")


def test_a_district_drilldown_shows_the_contender(house_contender):
    driver = house_contender()
    click_district(driver, "1601")
    wait_for_note(driver)
    assert driver.execute_script(CAND_ROWS) == ["Democrat", "Republican", "Independent"]
    assert len(driver.execute_script(TABLE_HEAD)) == 9
    note = driver.execute_script("return document.querySelector('#overlay-note').textContent;")
    assert "certain Independent" in note
    # the counties are filled purple: the independent leads in both
    fills = driver.execute_script("return [...document.querySelectorAll('#state-map path.county')].map(p => p.getAttribute('fill'));")
    assert fills and all(f in (token(driver, "--ind-certain"), token(driver, "--ind-projected"), token(driver, "--ind-faint")) for f in fills)
    assert not severe_logs(driver)


def test_a_legacy_district_payload_without_independent_fields_still_renders(site):
    """A cached proxy response from before the fields existed, for a district the map
    says has a contender: no crash, the independent's counties just read zero."""
    legacy = {k: v for k, v in STUB_ID1.items() if k not in ("indName", "indParty")}
    legacy["areas"] = [{k: v for k, v in a.items() if k != "indReal"} for a in STUB_ID1["areas"]]
    gen = house_pages(site, {"1601": legacy}, delay=0.05, default_viewport="desktop")
    open_page = next(gen)
    try:
        driver = open_page()
        click_district(driver, "1601")
        wait_for_note(driver)
        assert driver.execute_script(IS_OPEN)
        assert not [m for m in severe_logs(driver) if "favicon" not in m]
    finally:
        gen.close()
