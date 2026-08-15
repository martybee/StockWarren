"""Audit the dashboard colour palette in gui/static/css/style.css.

Run after ANY palette edit:

    venv/bin/python tests/audit_palette.py

This parses style.css rather than restating its values, so it cannot drift from
the stylesheet. It enforces the contract stated in that file's header comment
("every colour comes from a token") plus the accessibility bars the palette was
designed to meet.

Checks
  1. All theme blocks define an identical token-name set. Catches the classic
     "added the new tokens to five of the six blocks".
  2. --acct / --acct-tint are NOT defined in any theme block. They are locals set
     by the .acct-c* classes; a theme-level definition would silently override
     every var() fallback in the file.
  3. Every --accent-acct-N reaches WCAG AA (4.5:1) on bg-card, bg-secondary and
     bg-primary, since account-coloured text sits on all three.
  4. Every --accent-acct-N on its OWN tint clears that theme's existing
     .badge-* on-tint floor. This is a regression bar derived from the file
     itself, not an invented one -- the shipped badges already sit at 3.5-4.4:1.
  5. Account colours are far enough (CIEDE2000 >= 15) from green/red/blue, which
     mean profit/loss/interactive, and from each other.
"""
import pathlib
import re
import sys

CSS = pathlib.Path(__file__).resolve().parent.parent / "gui/static/css/style.css"
DELTA_E_MIN = 15.0
CONTRAST_MIN = 4.5
SURFACES = ("bg-card", "bg-secondary", "bg-primary")
SEMANTIC = ("accent-green", "accent-red", "accent-blue")


# ---------- colour maths ----------

def parse_colour(v):
    """'#rgb' | '#rrggbb' | 'rgba(r, g, b, a)' -> (r, g, b, a)."""
    v = v.strip()
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", v)
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)), a)
    h = v.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)


def over(fg, bg):
    """Composite a possibly-translucent colour over an opaque one."""
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)


def luminance(c):
    def f(x):
        x /= 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c[i]) for i in range(3))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def to_lab(c):
    def f(x):
        x /= 255.0
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c[i]) for i in range(3))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def g_(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)
    fx, fy, fz = g_(x), g_(y), g_(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(c1, c2):
    """CIEDE2000."""
    import math
    L1, a1, b1 = to_lab(c1)
    L2, a2, b2 = to_lab(c2)
    avg_L = (L1 + L2) / 2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    avg_C = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(avg_C ** 7 / (avg_C ** 7 + 25 ** 7))) if avg_C > 0 else 0
    a1p, a2p = a1 * (1 + G), a2 * (1 + G)
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    avg_Cp = (C1p + C2p) / 2
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2)
    if C1p * C2p == 0:
        avg_hp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        avg_hp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        avg_hp = (h1p + h2p + 360) / 2
    else:
        avg_hp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(avg_hp - 30))
         + 0.24 * math.cos(math.radians(2 * avg_hp))
         + 0.32 * math.cos(math.radians(3 * avg_hp + 6))
         - 0.20 * math.cos(math.radians(4 * avg_hp - 63)))
    Sl = 1 + (0.015 * (avg_L - 50) ** 2) / math.sqrt(20 + (avg_L - 50) ** 2)
    Sc = 1 + 0.045 * avg_Cp
    Sh = 1 + 0.015 * avg_Cp * T
    Rt = (-2 * math.sqrt(avg_Cp ** 7 / (avg_Cp ** 7 + 25 ** 7))
          * math.sin(math.radians(60 * math.exp(-(((avg_hp - 275) / 25) ** 2)))))
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


# ---------- parse ----------

def parse_themes(css):
    """{theme: {token-name: raw-value}} for every [data-theme="x"] block."""
    themes = {}
    for m in re.finditer(r'(?:^|\n)(?::root,\s*)?\[data-theme="(\w+)"\]\s*\{(.*?)\n\}', css, re.S):
        themes[m.group(1)] = dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", m.group(2)))
    return themes


def main():
    css = CSS.read_text()
    themes = parse_themes(css)
    if not themes:
        print("FAIL: no theme blocks parsed")
        return 1

    failures = []

    # --- 1. identical token-name sets across every theme -----------------
    names = {t: set(v) for t, v in themes.items()}
    reference = max(names.values(), key=len)
    for theme, got in names.items():
        missing, extra = reference - got, got - reference
        if missing or extra:
            failures.append(f"{theme}: token set differs (missing {sorted(missing)}, extra {sorted(extra)})")
    print(f"themes parsed: {', '.join(themes)}")
    print(f"tokens per theme: {len(reference)}  (identical across all: {not failures})")

    # --- 2. --acct/--acct-tint must be locals, never theme tokens --------
    for theme, toks in themes.items():
        for local in ("acct", "acct-tint"):
            if local in toks:
                failures.append(f"{theme}: defines --{local} as a theme token; it must be a .acct-c* local")

    slots = sorted(int(n.split("-")[-1]) for n in reference if re.fullmatch(r"accent-acct-\d", n))
    if not slots:
        print("FAIL: no --accent-acct-N tokens found")
        return 1

    for theme, toks in themes.items():
        col = {k: parse_colour(v) for k, v in toks.items()}

        # Regression bar taken from the file: how the shipped badges already read.
        badge_floor = min(
            contrast(col[f"accent-{h}"], over(col[f"tint-{h}"], col["bg-card"]))
            for h in ("green", "red", "blue", "yellow")
            if f"tint-{h}" in col and f"accent-{h}" in col
        )

        print(f"\n=== {theme} ===  (existing badge on-tint floor {badge_floor:.2f}:1)")
        for n in slots:
            acc = col[f"accent-acct-{n}"]
            tint = col[f"tint-acct-{n}"]
            surf = {s: contrast(acc, col[s]) for s in SURFACES}
            on_tint = contrast(acc, over(tint, col["bg-card"]))
            de_sem = {s.split("-")[1]: delta_e(acc, col[s]) for s in SEMANTIC}

            for s, v in surf.items():
                if v < CONTRAST_MIN:
                    failures.append(f"{theme} acct-{n}: {v:.2f}:1 on {s} (<{CONTRAST_MIN})")
            if on_tint < badge_floor:
                failures.append(f"{theme} acct-{n}: {on_tint:.2f}:1 on own tint (< badge floor {badge_floor:.2f})")
            for hue, d in de_sem.items():
                if d < DELTA_E_MIN:
                    failures.append(f"{theme} acct-{n}: dE {d:.1f} vs {hue} (<{DELTA_E_MIN})")

            print(f"  acct-{n} {toks[f'accent-acct-{n}']:9} "
                  f"card {surf['bg-card']:.2f} sec {surf['bg-secondary']:.2f} pri {surf['bg-primary']:.2f} "
                  f"| tint {on_tint:.2f} | dE g/r/b "
                  f"{de_sem['green']:.0f}/{de_sem['red']:.0f}/{de_sem['blue']:.0f}")

        # pairwise separation between the account slots themselves
        worst, pair = 999.0, None
        for i in slots:
            for j in slots:
                if i < j:
                    d = delta_e(col[f"accent-acct-{i}"], col[f"accent-acct-{j}"])
                    if d < worst:
                        worst, pair = d, (i, j)
        if worst < DELTA_E_MIN:
            failures.append(f"{theme}: slots {pair[0]}&{pair[1]} only dE {worst:.1f} apart (<{DELTA_E_MIN})")
        print(f"  closest pair: {pair[0]}&{pair[1]} dE {worst:.1f}")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL — {len(failures)} problem(s):")
        for f in failures:
            print("  -", f)
        return 1
    print(f"PASS — {len(themes)} themes x {len(slots)} account colours, all bars met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
