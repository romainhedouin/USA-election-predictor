"""The tab and the open drill-down live in the URL hash, so F5 stays put and a
link can be shared.

What could go wrong is mostly the unglamorous direction: a hash that is stale,
malformed or names something that cannot be opened must fall back to the
default view quietly, closing a drill-down by any route must clear it from the
URL (or a refresh reopens what was dismissed), and writing the URL must not
pile up history entries or disturb a visit that has no hash at all.
"""

import pytest
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from conftest import OPEN_STATE, STUB_CA01, click_district, house_pages, severe_logs, wait_for_note, wait_ready

IS_OPEN = "return document.querySelector('#overlay').classList.contains('open');"
TITLE = "return document.querySelector('#overlay-title').textContent;"
ACTIVE_TAB = ("return document.querySelector('#race-tabs button[aria-pressed=\"true\"]')"
              ".firstElementChild.textContent;")
CLICK_TAB = """
[...document.querySelectorAll('#race-tabs button')]
  .find(b => b.firstElementChild.textContent === arguments[0]).click();"""


def hash_of(driver):
    return driver.execute_script("return location.hash;")


def wait_tab(driver, label):
    WebDriverWait(driver, 25).until(lambda d: d.execute_script(ACTIVE_TAB) == label)


def wait_open(driver, title):
    WebDriverWait(driver, 25).until(
        lambda d: d.execute_script(IS_OPEN) and d.execute_script(TITLE) == title)


def test_no_hash_means_the_default_view_and_leaves_the_url_alone(page):
    driver = page("laptop")
    assert hash_of(driver) == ""
    assert driver.execute_script(ACTIVE_TAB) == "President"
    assert not driver.execute_script(IS_OPEN)


def test_tab_click_writes_the_hash_and_refresh_stays_on_the_tab(page):
    driver = page("laptop")
    driver.execute_script(CLICK_TAB, "Governor")
    wait_tab(driver, "Governor")
    assert hash_of(driver) == "#governor"

    driver.refresh()
    wait_ready(driver)
    assert driver.execute_script(ACTIVE_TAB) == "Governor"
    assert not severe_logs(driver)


def test_drilldown_survives_refresh(page):
    driver = page("laptop")
    driver.execute_script(CLICK_TAB, "Governor")
    wait_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE, "Pennsylvania")
    assert hash_of(driver) == "#governor/42"

    driver.refresh()
    wait_ready(driver)
    wait_open(driver, "Pennsylvania")
    assert driver.execute_script(ACTIVE_TAB) == "Governor"
    assert not severe_logs(driver)


def test_closing_the_drilldown_clears_it_from_the_url(page):
    """Every way out - Escape, the close button, the backdrop - must clear it,
    or a refresh would reopen something the viewer dismissed."""
    driver = page("laptop")
    for close in (
        lambda: driver.switch_to.active_element.send_keys(Keys.ESCAPE),
        lambda: driver.execute_script("document.querySelector('#overlay-close').click();"),
        lambda: driver.execute_script(
            "document.querySelector('#overlay').dispatchEvent(new MouseEvent('click', {bubbles: true}));"),
    ):
        driver.execute_script(OPEN_STATE, "Illinois")
        assert hash_of(driver) == "#president/17"
        close()
        assert not driver.execute_script(IS_OPEN)
        assert hash_of(driver) == "#president"

    driver.refresh()
    wait_ready(driver)
    assert not driver.execute_script(IS_OPEN)


def test_writing_the_url_adds_no_history_entries(page):
    driver = page("laptop")
    before = driver.execute_script("return history.length;")
    driver.execute_script(CLICK_TAB, "Governor")
    wait_tab(driver, "Governor")
    driver.execute_script(OPEN_STATE, "Pennsylvania")
    driver.execute_script("document.querySelector('#overlay-close').click();")
    assert driver.execute_script("return history.length;") == before


@pytest.mark.parametrize("bad", [
    "#nonsense",             # not a race
    "#governor/99",          # no such state
    "#governor/abc",
    "#governor/",            # trailing slash, empty unit
    "#governor/42/extra",    # extra segments are ignored, state still opens
    "#%E0%A4%A",             # malformed percent-escape: decodeURIComponent throws
    "#__proto__",            # must not be mistaken for a race
    "#constructor/17",
])
def test_garbage_hashes_fall_back_without_errors(page, base_url, bad):
    driver = page("laptop")
    driver.get(f"{base_url}/map.html{bad}")
    driver.refresh()
    wait_ready(driver)
    if bad == "#governor/42/extra":
        wait_open(driver, "Pennsylvania")
    else:
        assert not driver.execute_script(IS_OPEN)
    assert driver.execute_script(ACTIVE_TAB) in ("President", "Governor")
    assert not severe_logs(driver)


def test_pasting_another_link_into_an_open_tab_navigates(page, base_url):
    """Changing only the hash is a same-document navigation: nothing reloads,
    so the page has to notice by itself."""
    driver = page("laptop")
    driver.get(f"{base_url}/map.html#governor/42")
    wait_open(driver, "Pennsylvania")
    assert driver.execute_script(ACTIVE_TAB) == "Governor"

    driver.get(f"{base_url}/map.html#president/17")
    wait_tab(driver, "President")
    wait_open(driver, "Illinois")

    driver.get(f"{base_url}/map.html#president")
    WebDriverWait(driver, 25).until(lambda d: not d.execute_script(IS_OPEN))
    assert hash_of(driver) == "#president"
    assert not severe_logs(driver)


@pytest.fixture
def house_site(site):
    yield from house_pages(site, {"0601": STUB_CA01}, delay=0.1, default_viewport="desktop")


def test_house_district_survives_refresh(house_site):
    driver = house_site()
    assert hash_of(driver) == "#house"
    click_district(driver, "0601")
    wait_for_note(driver)
    assert hash_of(driver) == "#house/0601"

    driver.refresh()
    wait_ready(driver)
    wait_open(driver, "California District 1")
    wait_for_note(driver)
    assert driver.execute_script(ACTIVE_TAB) == "House"
    assert not severe_logs(driver)


def test_house_link_to_an_unknown_district_falls_back(house_site):
    driver = house_site()
    base = driver.current_url.split("/map.html")[0]
    driver.get(f"{base}/map.html#house/9999")
    driver.refresh()
    wait_ready(driver)
    assert driver.execute_script(ACTIVE_TAB) == "House"
    assert not driver.execute_script(IS_OPEN)
    assert not severe_logs(driver)
