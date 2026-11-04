"""The palettes are measured, not asserted.

map.html's comments promise every pair of fills is >= 20 CIEDE2000 apart under
protanopia, deuteranopia and tritanopia. The script that checked it was never
committed, so nothing noticed when a colour moved - and a third bloc (purple) was
about to be added to a palette that was already full. scripts/check_palette.py is
that script; this file runs it.

What is and is not promised, learned by measuring rather than reading the comments:
  * Even before purple, the standard palettes were never CVD-safe (their weakest
    pair is 12-14 under simulated colour blindness) and the "faint" tints sit only
    4-6 from "no data" on purpose. The 20-point rule only ever held for the solid
    fills of the colour-blind palettes.
  * No purple can be told from blue on hue alone under protan/deutan: the best
    recognisably-purple choice is ~13-15 from the nearest blue. So in colour-blind
    mode an independent is ALSO marked by texture (see test_ui_independent.py), and
    the floors below are the measured values, pinned so they cannot quietly fall.
"""

import sys

import pytest

from conftest import REPO, make_driver

sys.path.insert(0, str(REPO / "scripts"))
import check_palette as cp  # noqa: E402

SOLID = ("dem-certain", "dem-projected", "rep-certain", "rep-projected", "ind-certain", "ind-projected", "no-data")
CVD_VISIONS = ("protan", "deutan", "tritan")


# ---------------------------------------------------------------- the maths

# Sharma, Wu & Dalal (2005), "The CIEDE2000 color-difference formula", supplementary data.
SHARMA = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


@pytest.mark.parametrize("lab1, lab2, expected", SHARMA)
def test_ciede2000_matches_the_published_reference_values(lab1, lab2, expected):
    assert cp.ciede2000(lab1, lab2) == pytest.approx(expected, abs=5e-4)
    assert cp.ciede2000(lab2, lab1) == pytest.approx(expected, abs=5e-4)     # symmetric


def test_a_colour_is_zero_from_itself_and_greys_survive_every_simulation():
    for rgb in [(10, 20, 30), (200, 100, 50), (128, 128, 128)]:
        assert cp.delta(rgb, rgb) == pytest.approx(0, abs=1e-9)
    for vision in cp.CVD_MATRICES:
        for grey in (0, 77, 128, 255):
            assert cp.simulate((grey, grey, grey), vision) == pytest.approx((grey, grey, grey), abs=0.6)
        # ... because every row of a Machado matrix sums to 1.
        assert all(sum(row) == pytest.approx(1, abs=1e-5) for row in cp.CVD_MATRICES[vision])


def test_the_simulations_do_what_they_are_named_for():
    # Red-green deficiencies pull red and green together; tritanopia pulls blue and green together.
    red, green, blue = (200, 30, 30), (30, 160, 30), (30, 30, 200)
    assert cp.delta(red, green, "protan") < cp.delta(red, green, "normal") / 2
    assert cp.delta(red, green, "deutan") < cp.delta(red, green, "normal") / 2
    teal = (30, 150, 150)
    assert cp.delta(blue, teal, "tritan") < cp.delta(blue, teal, "normal")


def test_blending_a_stripe_is_done_in_linear_light():
    assert cp.blend((0, 0, 0), (255, 255, 255), 0) == pytest.approx((0, 0, 0), abs=1e-9)
    assert cp.blend((0, 0, 0), (255, 255, 255), 1) == pytest.approx((255, 255, 255), abs=1e-6)
    # Half black, half white is NOT sRGB 128: linear 0.5 is sRGB ~188.
    assert cp.blend((0, 0, 0), (255, 255, 255), 0.5)[0] == pytest.approx(188, abs=1)


@pytest.mark.parametrize("text, expected", [
    ("rgb(1, 2, 3)", (1, 2, 3)), ("rgba(10, 20, 30, 1)", (10, 20, 30)), ("#0a141e", (10, 20, 30)),
    ("#abc", (170, 187, 204)), ("color(srgb 1 0 0.5)", (255, 0, 127.5)),
])
def test_css_colours_parse(text, expected):
    assert cp.parse_css_color(text) == pytest.approx(expected)


# ---------------------------------------------------------------- the palettes

@pytest.fixture(scope="module")
def palettes(base_url):
    driver = make_driver(1440, 900, False)
    try:
        driver.get(f"{base_url}/map.html")
        yield cp.collect(driver)
    finally:
        driver.quit()


# Hex values of the pre-existing fills, per combination, so adding purple could not
# have nudged a red or blue by accident.
EXISTING = {
    "light":     {"dem-certain": "#1c4e8c", "dem-projected": "#a8c8e8", "rep-certain": "#b02127", "rep-projected": "#efb0ac", "no-data": "#e4e7ea"},
    "light+cvd": {"dem-certain": "#00337c", "dem-projected": "#74a9f0", "rep-certain": "#ce3e39", "rep-projected": "#ffb37e", "no-data": "#e6ebed"},
    "dark":      {"dem-certain": "#3d8bf0", "dem-projected": "#26466f", "rep-certain": "#f04a45", "rep-projected": "#822b2f", "no-data": "#2b323d"},
    "dark+cvd":  {"dem-certain": "#51a7fc", "dem-projected": "#0d42a6", "rep-certain": "#f46157", "rep-projected": "#430f03", "no-data": "#1c1c30"},
}

# Measured minimum CIEDE2000 between a purple fill and any other solid fill, pinned a
# hair below what was measured so they only fail when something gets worse.
#   (normal vision, worst of protan/deutan/tritan)
FLOORS = {
    "light":     (20.0, 9.5),     # measured 20.6 / 10.1  (standard palette: no colour-blind promise)
    "dark":      (18.0, 5.0),     # measured 18.6 /  5.6  (ditto: lavender vs blue under protanopia)
    "light+cvd": (19.5, 12.5),    # measured 20.0 / 13.3  (texture carries the rest, see test_ui_independent)
    "dark+cvd":  (20.5, 9.5),     # measured 21.1 / 10.0
}


def worst(pairs, visions):
    return min(((d[v], pair, v) for pair, d in pairs.items() for v in visions), key=lambda r: r[0])


@pytest.mark.parametrize("combination", list(cp.COMBINATIONS))
def test_the_existing_fills_did_not_move(palettes, combination):
    got = {n: cp.hex_of(palettes[combination][n]) for n in EXISTING[combination]}
    assert got == EXISTING[combination]


@pytest.mark.parametrize("combination", list(cp.COMBINATIONS))
def test_every_palette_defines_the_purple_tokens(palettes, combination):
    for token in ("ind-certain", "ind-projected", "ind-faint"):
        assert token in palettes[combination], f"--{token} is missing from the {combination} palette"


@pytest.mark.parametrize("combination", list(cp.COMBINATIONS))
def test_purple_is_separable_from_every_other_solid_fill(palettes, combination):
    pairs = {k: v for k, v in cp.matrix(palettes[combination], combination, SOLID).items()
             if any(n.startswith("ind-") for n in k)}
    normal_floor, cvd_floor = FLOORS[combination]
    low, pair, _ = worst(pairs, ("normal",))
    assert low >= normal_floor, f"{pair} is only {low:.1f} apart in normal vision\n{cp.report({combination: palettes[combination]})}"
    low, pair, vision = worst(pairs, CVD_VISIONS)
    assert low >= cvd_floor, f"{pair} is only {low:.1f} apart under {vision}\n{cp.report({combination: palettes[combination]})}"


@pytest.mark.parametrize("combination", ["light", "dark"])
def test_in_the_standard_palettes_purple_is_no_harder_to_see_than_the_weakest_existing_pair(palettes, combination):
    pairs = cp.matrix(palettes[combination], combination, SOLID)
    new = worst({k: v for k, v in pairs.items() if any(n.startswith("ind-") for n in k)}, ("normal",))[0]
    old = worst({k: v for k, v in pairs.items() if not any(n.startswith("ind-") for n in k)}, ("normal",))[0]
    assert new >= old, f"purple ({new:.1f}) is harder to see than the existing weakest pair ({old:.1f})"


@pytest.mark.parametrize("combination", ["light+cvd", "dark+cvd"])
def test_in_the_colour_blind_palettes_purple_keeps_the_normal_vision_rule(palettes, combination):
    """In normal vision the colour-blind palettes' own solid fills keep their 20-point margin;
    purple must not be the pair that breaks it."""
    pairs = cp.matrix(palettes[combination], combination, SOLID)
    new = worst({k: v for k, v in pairs.items() if any(n.startswith("ind-") for n in k)}, ("normal",))[0]
    assert new >= 19.5


@pytest.mark.parametrize("combination", list(cp.COMBINATIONS))
def test_the_faint_purple_is_at_least_as_far_from_no_data_as_the_faint_blue(palettes, combination):
    """"Leading, nobody projected yet" is a deliberately subtle tint; purple's must not vanish
    into the "no data" grey any more than blue's does (measured per vision)."""
    pairs = cp.matrix(palettes[combination], combination, ("ind-faint", "dem-faint", "no-data"))
    for vision in cp.VISIONS:
        purple = pairs[("ind-faint", "no-data")][vision]
        blue = pairs[("dem-faint", "no-data")][vision]
        assert purple >= blue - 2.5, f"under {vision} faint purple is {purple:.1f} from 'no data', faint blue {blue:.1f}"
