"""Colour separation of map.html's palettes, measured instead of asserted.

map.html's comments promise that every pair of map fills is at least 20 CIEDE2000
apart under simulated protanopia, deuteranopia and tritanopia. The script that
checked that was never committed, so nothing could re-check it when a colour
changed. This is that script, and tests/test_palette.py runs it.

    python scripts/check_palette.py            # print every palette's matrix

Method
  * Colours are read from the page as the browser resolves them (the real
    cascade, including color-mix() tints), for the four combinations of theme
    (light/dark) and palette (standard/colour-blind).
  * Colour-blindness is simulated with Machado, Oliveira & Fernandes (2009),
    severity 1.0, applied in linear light.
  * Distance is CIEDE2000 (Sharma, Wu & Dalal 2005), validated against their
    published test pairs in tests/test_palette.py.
  * In the colour-blind palette "projected" and "faint" fills are stripes, not
    flat colour. What the eye averages is a blend of base and stripe in linear
    light, weighted by how much of the tile the stripe covers; that blend is what
    is measured (see HATCHED).

Only the standard library is needed for the maths; selenium is imported lazily,
by collect(), so the maths can be imported anywhere.
"""

import math
import sys
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- colour maths

D65 = (0.95047, 1.0, 1.08883)

# Machado et al. (2009), severity 1.0, on linear sRGB.
CVD_MATRICES = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}
VISIONS = ("normal", "protan", "deutan", "tritan")


def to_linear(channel):
    """sRGB 0-255 -> linear light 0-1."""
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def from_linear(c):
    c = min(1.0, max(0.0, c))
    return 255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)


def simulate(rgb, vision):
    """rgb (0-255 floats) as someone with `vision` sees it."""
    if vision == "normal":
        return tuple(rgb)
    lin = [to_linear(c) for c in rgb]
    return tuple(from_linear(sum(m * v for m, v in zip(row, lin))) for row in CVD_MATRICES[vision])


def to_lab(rgb):
    r, g, b = (to_linear(c) for c in rgb)
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x / D65[0]), f(y / D65[1]), f(z / D65[2])
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(lab1, lab2):
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_bar7 = ((c1 + c2) / 2) ** 7
    g = 0.5 * (1 - math.sqrt(c_bar7 / (c_bar7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (b1 or a1p) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (b2 or a2p) else 0.0

    dL, dC = L2 - L1, c2p - c1p
    if c1p * c2p == 0:
        dh = 0.0
    else:
        dh = h2p - h1p
        dh -= 360 if dh > 180 else 0
        dh += 360 if dh < -180 else 0
    dH = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))

    L_bar, c_bar = (L1 + L2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        h_bar = (h1p + h2p) / 2
    else:
        h_bar = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2

    t = (1 - 0.17 * math.cos(math.radians(h_bar - 30)) + 0.24 * math.cos(math.radians(2 * h_bar))
         + 0.32 * math.cos(math.radians(3 * h_bar + 6)) - 0.20 * math.cos(math.radians(4 * h_bar - 63)))
    d_theta = 30 * math.exp(-(((h_bar - 275) / 25) ** 2))
    rc = 2 * math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25 ** 7))
    sl = 1 + 0.015 * (L_bar - 50) ** 2 / math.sqrt(20 + (L_bar - 50) ** 2)
    sc, sh = 1 + 0.045 * c_bar, 1 + 0.015 * c_bar * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc
    return math.sqrt((dL / sl) ** 2 + (dC / sc) ** 2 + (dH / sh) ** 2 + rt * (dC / sc) * (dH / sh))


def delta(rgb1, rgb2, vision="normal"):
    return ciede2000(to_lab(simulate(rgb1, vision)), to_lab(simulate(rgb2, vision)))


def blend(base, stripe, coverage):
    """What the eye averages over a striped tile: base and stripe mixed in LINEAR
    light, `coverage` being the fraction of the tile the stripe covers."""
    return tuple(from_linear((1 - coverage) * to_linear(b) + coverage * to_linear(s)) for b, s in zip(base, stripe))


def hex_of(rgb):
    return "#" + "".join(f"{round(min(255, max(0, c))):02x}" for c in rgb)


def parse_css_color(text):
    """'rgb(1, 2, 3)', 'rgba(...)', 'color(srgb 0.1 0.2 0.3)' or '#rrggbb' -> (r, g, b) 0-255."""
    text = text.strip()
    if text.startswith("#"):
        text = text[1:]
        if len(text) == 3:
            text = "".join(c * 2 for c in text)
        return tuple(float(int(text[i:i + 2], 16)) for i in (0, 2, 4))
    inner = text[text.index("(") + 1:text.rindex(")")]
    if text.startswith("color("):
        parts = inner.replace("/", " ").split()
        assert parts[0] == "srgb", f"unexpected colour space in {text!r}"
        return tuple(255 * float(p) for p in parts[1:4])
    nums = [p for p in inner.replace("/", ",").replace(" ", ",").split(",") if p]
    return tuple(float(n) for n in nums[:3])


# --------------------------------------------------------------- the palettes

# The four ways the page can look. Names are what tests print.
COMBINATIONS = {
    "light":     {"theme": "light", "palette": "standard"},
    "light+cvd": {"theme": "light", "palette": "cvd"},
    "dark":      {"theme": "dark",  "palette": "standard"},
    "dark+cvd":  {"theme": "dark",  "palette": "cvd"},
}

# The flat fills, as CSS custom properties.
TOKENS = ("dem-certain", "dem-projected", "dem-faint", "rep-certain", "rep-projected", "rep-faint",
          "ind-certain", "ind-projected", "ind-faint", "no-data")


def collect(driver):
    """{combination: {token: (r, g, b)}} for every token the page defines, read as
    the browser resolves them. Tokens a page does not define are left out."""
    script = """
    const [theme, palette, tokens] = arguments;
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);
    if (palette === 'cvd') root.setAttribute('data-palette', 'cvd'); else root.removeAttribute('data-palette');
    const out = {};
    for (const token of tokens) {
      if (!getComputedStyle(root).getPropertyValue('--' + token).trim()) continue;
      const probe = document.createElement('div');
      probe.style.background = 'var(--' + token + ')';
      document.body.appendChild(probe);
      out[token] = getComputedStyle(probe).backgroundColor;
      probe.remove();
    }
    return out;"""
    result = {}
    for name, combo in COMBINATIONS.items():
        raw = driver.execute_script(script, combo["theme"], combo["palette"], list(TOKENS))
        result[name] = {token: parse_css_color(value) for token, value in raw.items()}
    return result


# What a striped fill looks like once the eye has averaged it: (base token,
# stripe token, fraction of the tile the stripe covers). Mirrors buildDefs in
# map.html; the colour-blind palette is the only one that stripes.
HATCHED = {
    "dem-projected": ("dem-projected", "dem-certain", 0.28),
    "rep-projected": ("rep-projected", "rep-certain", 0.28),
    "ind-projected": ("ind-projected", "ind-certain", 0.28),
    "dem-faint":     ("no-data", "dem-projected", 0.16),
    "rep-faint":     ("no-data", "rep-projected", 0.16),
    "ind-faint":     ("no-data", "ind-certain", 0.12),     # darker and thinner than the others, see buildDefs
}


def appearance(tokens, name, combination):
    """The colour a fill is judged by: the flat token, or in the colour-blind
    palette the striped blend for projected and faint fills."""
    if name in HATCHED and COMBINATIONS[combination]["palette"] == "cvd":
        base, stripe, coverage = HATCHED[name]
        if base in tokens and stripe in tokens:
            return blend(tokens[base], tokens[stripe], coverage)
    return tokens[name]


def matrix(tokens, combination, names=None):
    """{(a, b): {vision: delta}} for every pair of fills present."""
    names = [n for n in (names or TOKENS) if n in tokens]
    colours = {n: appearance(tokens, n, combination) for n in names}
    return {(a, b): {v: delta(colours[a], colours[b], v) for v in VISIONS} for a, b in combinations(names, 2)}


def weakest(pairs, visions=VISIONS):
    """(distance, pair, vision) of the closest two fills under any of `visions`."""
    return min(((d[v], pair, v) for pair, d in pairs.items() for v in visions), key=lambda r: r[0])


def report(palettes):
    lines = []
    for combination, tokens in palettes.items():
        pairs = matrix(tokens, combination)
        lines.append(f"\n== {combination}  (" + ", ".join(f"{n} {hex_of(appearance(tokens, n, combination))}" for n in TOKENS if n in tokens) + ")")
        lines.append(f"   {'pair':34}" + "".join(f"{v:>8}" for v in VISIONS))
        for (a, b), d in sorted(pairs.items(), key=lambda kv: min(kv[1].values())):
            lines.append(f"   {a + ' / ' + b:34}" + "".join(f"{d[v]:8.1f}" for v in VISIONS))
        low, pair, vision = weakest(pairs)
        lines.append(f"   weakest: {low:.1f}  {pair[0]} / {pair[1]} under {vision}")
    return "\n".join(lines)


def main():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get((REPO / "map.html").as_uri())
        print(report(collect(driver)))
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
