#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

    python scripts/render_cards.py            -> cards/*.svg          (animated)
    STATIC=1 python scripts/render_cards.py   -> cards-static/*.svg   (final frame)
                                                 + cards-static/preview.html

Built for <img> embedding, so it uses only what works there: an inline <style>
block with CSS keyframes, SMIL <animate>, and fonts subset with pyftsubset and
embedded as base64 WOFF data URIs. No scripts, no external requests.

Reduced motion
  * CSS keyframes only define a `from` state (fill-mode: backwards), so removing
    the animation leaves every element in its final, visible state.
    `prefers-reduced-motion: reduce` sets `animation: none`.
  * SMIL can't be switched off from CSS, so it is used only for decorative
    loops (the pulsing dot). Those carry class `deco`, `display:none` under
    reduced motion.

Input : data/contributions.json (refreshed daily by the GitHub Action)
Optional: theme.json (colour overrides), assets/portrait.{jpg,png,webp}
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC", "").lower() not in ("", "0", "false", "no")
ANIM = not STATIC
OUT = ROOT / ("cards-static" if STATIC else "cards")
DATA = ROOT / "data" / "contributions.json"
FONT_DIR = ROOT / "fonts"
W = 830  # GitHub's README column is ~830px wide


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# ───────────────────────────── content ──────────────────────────────────────
P = dict(
    first="Yeabsera",
    last="Sisay",
    highlight=3,  # index of the letter in the last name drawn in blue ("Sisay" -> the "a")
    eyebrow="AI ENGINEER · ADDIS ABABA (UTC+3)",
    about="I build intelligent systems that can see, hear, reason, and act.",
    link="yeabsera-sisay.vercel.app",
)
LINKS = dict(
    site="https://yeabsera-sisay.vercel.app/",
    linkedin="https://www.linkedin.com/in/yeabsera-sisay-b5615b240",
    mail="mailto:g4uforlife@gmail.com",
)

# Three coloured tiles (each is linked from the README). num is upright, word italic.
TILES = [
    dict(slug="build", eyebrow="BUILD", num="10", word="systems", bg="blue", fg="#ffffff",
         desc="Agents, voice AI, vision and local search, shipped end to end."),
    dict(slug="win", eyebrow="WIN", num="1st", word="place", bg="red", fg="#ffffff",
         desc="UniHack / Agent.ai for IDA. Second at the African Blockchain Championship."),
    dict(slug="talk", eyebrow="CONNECT", num="Say", word="hello", bg="yellow", fg="#111111",
         desc="g4uforlife@gmail.com. Usually replies within 24 hours."),
]

# "What I'm coding": one white card each, three per row. repo must match the GitHub repo name.
FEATURED = [
    dict(repo="omniq", title="omniq",
         desc="Voice AI for people without smartphones, over 2G/PSTN with realtime LLMs and IVR."),
    dict(repo="medscop", title="medscop",
         desc="Epidemic simulation with specialised AI agents for policy, logistics and response."),
    dict(repo="attlas", title="attlas",
         desc="Turns GitHub repos and resumes into deployable portfolios in under 45 seconds."),
]

# ───────────────────────────── theme ────────────────────────────────────────
# Sampled by eye from your reference video. Override any key in theme.json.
THEME = dict(
    page="#0d1117",
    cream="#f8f7f2",     # big cards
    white="#ffffff",     # repo cards
    line="#e6e4dc",      # hairlines and card edges
    ink="#0b0b0b",
    muted="#76746d",
    blue="#1b3dff",      # brand colour: graph, peak bar, accents, BUILD tile
    red="#dc1a1f",
    yellow="#ffb703",
    grey_bar="#c9c8c2",
    portrait_invert=False,  # True if your portrait is light-on-dark
)
_tf = ROOT / "theme.json"
if _tf.exists():
    THEME.update(json.loads(_tf.read_text()))
T = THEME


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, t: float) -> str:
    return "#%02x%02x%02x" % tuple(round(x * (1 - t) + y * t) for x, y in zip(_rgb(a), _rgb(b)))


# ───────────────────────────── fonts ────────────────────────────────────────
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif"
SANS = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
IS = GF + "instrumentsans/InstrumentSans%5Bwdth%2Cwght%5D.ttf"

# role -> spec. Variable fonts are pinned (every axis) to one instance per role,
# then subset per card to only the characters that card uses.
FONTS = {
    "serif": dict(cls="fr", alias="ys-serif", file="InstrumentSerif-Regular.ttf",
                  url=GF + "instrumentserif/InstrumentSerif-Regular.ttf", axes={}, weight=400, style="normal", stack=SERIF),
    "serifi": dict(cls="fi", alias="ys-serifi", file="InstrumentSerif-Italic.ttf",
                   url=GF + "instrumentserif/InstrumentSerif-Italic.ttf", axes={}, weight=400, style="italic", stack=SERIF),
    "sans": dict(cls="fs", alias="ys-sans", file="InstrumentSans-VF.ttf", url=IS,
                 axes={"wght": 400, "wdth": 100}, weight=400, style="normal", stack=SANS),
    "sansb": dict(cls="fb", alias="ys-sansb", file="InstrumentSans-VF.ttf", url=IS,
                  axes={"wght": 600, "wdth": 100}, weight=600, style="normal", stack=SANS),
    "mono": dict(cls="fm", alias="ys-mono", file="JetBrainsMono-VF.ttf",
                 url=GF + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
                 axes={"wght": 500}, weight=500, style="normal", stack=MONO),
}


class FontBook:
    def __init__(self) -> None:
        self.tt, self.path, self.cmap, self.upem = {}, {}, {}, {}
        self.tmp = Path(tempfile.mkdtemp(prefix="cardfonts-"))
        try:
            from fontTools.ttLib import TTFont
            from fontTools.varLib import instancer
        except ImportError:
            warn("fonttools not installed (pip install fonttools); using system fonts")
            return
        FONT_DIR.mkdir(exist_ok=True)
        for role, spec in FONTS.items():
            src = FONT_DIR / spec["file"]
            if not src.exists():
                try:
                    print(f"downloading {spec['file']} ...")
                    urllib.request.urlretrieve(spec["url"], src)
                except Exception as e:  # noqa: BLE001
                    warn(f"could not fetch {spec['file']} ({e}); '{role}' falls back to a system font")
                    continue
            font = TTFont(src)
            if "fvar" in font:
                limits = {}
                for ax in font["fvar"].axes:
                    v = spec["axes"].get(ax.axisTag, ax.defaultValue)
                    limits[ax.axisTag] = max(ax.minValue, min(ax.maxValue, v))
                font = instancer.instantiateVariableFont(font, limits)
            p = self.tmp / f"inst-{role}.ttf"
            font.save(p)
            f2 = TTFont(p)
            self.path[role], self.tt[role] = p, f2
            self.cmap[role] = f2.getBestCmap()
            self.upem[role] = f2["head"].unitsPerEm

    def width(self, role: str, size: float, s: str, ls: float = 0) -> float:
        if role not in self.tt:
            k = 0.62 if role == "mono" else 0.5
            return len(s) * size * k + ls * len(s)
        hm, cm = self.tt[role]["hmtx"], self.cmap[role]
        tot = sum(hm[cm[ord(ch)]][0] if ord(ch) in cm else self.upem[role] * .5 for ch in s)
        return tot / self.upem[role] * size + ls * len(s)

    def face(self, role: str, chars: set[str]) -> str:
        if role not in self.tt:
            return ""
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "chars.txt").write_text("".join(sorted(chars | {" "})), encoding="utf-8")
        out = d / "s.woff"
        exe = shutil.which("pyftsubset")
        cmd = [exe] if exe else [sys.executable, "-m", "fontTools.subset"]
        cmd += [str(self.path[role]), f"--text-file={d / 'chars.txt'}", "--flavor=woff",
                "--no-hinting", f"--output-file={out}"]
        subprocess.run(cmd, check=True, capture_output=True)
        b64 = base64.b64encode(out.read_bytes()).decode()
        return (f'@font-face{{font-family:"{FONTS[role]["alias"]}";font-weight:400;font-style:normal;'
                f'src:url(data:font/woff;base64,{b64}) format("woff")}}')


FB: FontBook


def fit(role, size, s, maxw, lo=9, ls=0):
    while size > lo and FB.width(role, size, s, ls) > maxw:
        size -= .5
    return size


def wrap(role, size, s, maxw):
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and FB.width(role, size, trial) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


# ───────────────────────────── card plumbing ────────────────────────────────
CSS_ANIM = """
@keyframes rise{from{opacity:0;transform:translateY(12px)}}
@keyframes blur{from{opacity:0;transform:translateY(10px);filter:blur(9px)}}
@keyframes fade{from{opacity:0}}
@keyframes pop{from{opacity:0;transform:scale(.5)}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
.a{animation-duration:.8s;animation-timing-function:cubic-bezier(.2,.7,.2,1);animation-fill-mode:backwards;animation-delay:var(--d,0s)}
.rise{animation-name:rise}.fade{animation-name:fade}
.blur{animation-name:blur;animation-duration:1.1s}
.pop{animation-name:pop;transform-box:fill-box;transform-origin:center}
.grow{animation-name:grow;transform-box:fill-box;transform-origin:50% 100%}
.growx{animation-name:growx;transform-box:fill-box;transform-origin:0 50%}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}.deco{display:none}}
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def A(d, kind="rise", cls="") -> str:
    """class + stagger-delay attributes for a non-text element (plain in STATIC)."""
    if not ANIM or d is None:
        return f' class="{cls}"' if cls else ""
    return f' class="{(cls + " ").lstrip()}a {kind}" style="--d:{d:.2f}s"'


def fmt(n) -> str:
    return f"{int(n):,}"


class Card:
    def __init__(self, name, w, h, label):
        self.name, self.w, self.h, self.label = name, w, h, label
        self.defs, self.body, self.chars, self.css_extra = [], [], {}, ""

    def put(self, *parts):
        self.body.extend(parts)

    def bg(self, fill, rx=16, stroke=None):
        st = f' stroke="{stroke}"' if stroke else ""
        self.put(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{rx}" fill="{fill}"{st}/>')

    def text(self, x, y, s, role, size, fill, anchor="start", ls=0, d=None, kind="rise"):
        self.chars.setdefault(role, set()).update(s)
        cls, st = FONTS[role]["cls"], ""
        if ANIM and d is not None:
            cls += f" a {kind}"
            st = f' style="--d:{d:.2f}s"'
        anch = "" if anchor == "start" else f' text-anchor="{anchor}"'
        lsa = f' letter-spacing="{ls}"' if ls else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-size="{size}" fill="{fill}"{anch}{lsa}{st}>'
                f'{esc(s)}</text>')

    def runs(self, x, y, parts, size, d=None, kind="rise", anchor="start"):
        """Place several (text, role, fill) runs on one baseline using real glyph widths."""
        total = sum(FB.width(r, size, s) for s, r, _ in parts)
        cx = x - (total if anchor == "end" else 0)
        out = []
        for s, role, fill in parts:
            out.append(self.text(cx, y, s, role, size, fill, d=d, kind=kind))
            cx += FB.width(role, size, s)
        return "".join(out)

    def svg(self) -> str:
        css = ["text{text-rendering:geometricPrecision}"]
        for role in self.chars:
            sp = FONTS[role]
            face = FB.face(role, self.chars[role])
            if face:
                css += [face, f'.{sp["cls"]}{{font-family:"{sp["alias"]}",{sp["stack"]}}}']
            else:  # fallback: ask the system font for the real weight/style
                css.append(f'.{sp["cls"]}{{font-family:{sp["stack"]};font-weight:{sp["weight"]};font-style:{sp["style"]}}}')
        css.append(self.css_extra)
        if ANIM:
            css.append(CSS_ANIM)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
                f'<title>{esc(self.label)}</title><style>{"".join(css)}</style>'
                f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>')


# the page plays top to bottom (seconds)
TL = dict(hero=0.0, graph=1.3, stats=2.0, tiles=[2.9, 3.05, 3.2], heading=3.7, repos=[3.9, 4.05, 4.2])

# ───────────────────────────── data ─────────────────────────────────────────


def load_data():
    raw = json.loads(DATA.read_text())
    days: dict[str, int] = {}
    if "days" in raw:
        days = {d["date"]: int(d.get("count", d.get("contributionCount", 0))) for d in raw["days"]}
    else:
        cal = raw.get("data", raw)
        if "user" in cal:
            cal = cal["user"]["contributionsCollection"]["contributionCalendar"]
        elif "contributionCalendar" in cal:
            cal = cal["contributionCalendar"]
        for wk in cal["weeks"]:
            for d in wk["contributionDays"]:
                days[d["date"]] = int(d["contributionCount"])
    if not days:
        raise SystemExit(f"no contribution days found in {DATA}")
    repos = {r["name"].lower(): r for r in raw.get("repos", [])}
    return {dt.date.fromisoformat(k): v for k, v in days.items()}, repos


def stats(days):
    today = max(days)
    start = today - dt.timedelta(days=364)
    win = {d: n for d, n in days.items() if start <= d <= today}
    # streaks: today may still be empty, so the current streak counts back from yesterday then
    d = today if win.get(today, 0) > 0 else today - dt.timedelta(days=1)
    streak = 0
    while win.get(d, 0) > 0:
        streak, d = streak + 1, d - dt.timedelta(days=1)
    longest = run = 0
    for k in sorted(win):
        run = run + 1 if win[k] > 0 else 0
        longest = max(longest, run)
    best_n, best_d = max(((n, k) for k, n in win.items()), key=lambda t: (t[0], t[1]))
    ym, (y, m) = [], (today.year, today.month)
    for _ in range(12):
        ym.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    ym.reverse()
    months = [(dt.date(y, m, 1).strftime("%b"), sum(n for k, n in win.items() if (k.year, k.month) == (y, m)))
              for y, m in ym]
    nxt = today + dt.timedelta(days=1)
    return dict(today=today, start=start, win=win, total=sum(win.values()),
                active=sum(1 for n in win.values() if n > 0), streak=max(streak, 0), longest=max(longest, streak),
                busiest=(best_n, best_d), months=months, window_days=len(win),
                partial=nxt.month == today.month)  # current month still in progress


# ───────────────────────────── halftone art ─────────────────────────────────


def halftone(cols: int, rows: int):
    """Return [(col,row,darkness 0..1)]: from assets/portrait.* if present, else a ripple."""
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = ROOT / "assets" / f"portrait.{ext}"
        if p.exists():
            try:
                from PIL import Image, ImageFilter, ImageOps
                im = ImageOps.fit(Image.open(p).convert("L"), (cols, rows), Image.LANCZOS, centering=(.5, .38))
                im = ImageOps.autocontrast(im.filter(ImageFilter.GaussianBlur(.4)), cutoff=2)
                px = im.load()
                inv = bool(T.get("portrait_invert"))
                return [(c, r, (px[c, r] / 255) if inv else 1 - px[c, r] / 255) for r in range(rows) for c in range(cols)]
            except ImportError:
                warn("Pillow missing (pip install pillow); using the generated halftone instead of your portrait")
    out = []
    for r in range(rows):
        for c in range(cols):
            x, y = c / cols, r / rows
            dist = math.hypot(x - .52, (y - .5) * .95)
            wave = .5 + .5 * math.cos(dist * 34)
            out.append((c, r, wave * max(0.0, 1 - dist * 1.55)))
    return out


# ───────────────────────────── cards ────────────────────────────────────────


def card_hero() -> Card:
    h, pad = 368, 40
    c = Card("hero", W, h, f'{P["first"]} {P["last"]}. {P["about"]}')
    c.bg(T["cream"], 16, T["line"])
    t0 = TL["hero"]
    # eyebrow
    c.put(f'<g{A(t0 + .1)}><circle cx="{pad + 3}" cy="52" r="3.2" fill="{T["blue"]}"/>'
          + (f'<circle class="deco" cx="{pad + 3}" cy="52" r="3.2" fill="none" stroke="{T["blue"]}" stroke-width="1">'
             f'<animate attributeName="r" values="3.2;10" dur="2.6s" begin="1s" repeatCount="indefinite"/>'
             f'<animate attributeName="opacity" values=".7;0" dur="2.6s" begin="1s" repeatCount="indefinite"/></circle>' if ANIM else "")
          + "</g>")
    c.put(c.text(pad + 16, 56, P["eyebrow"], "mono", 10.5, T["muted"], ls=1.4, d=t0 + .1))
    # name: first upright, last italic with one blue letter
    size, avail = 100, 440
    while size > 50 and max(FB.width("serif", size, P["first"]), FB.width("serifi", size, P["last"])) > avail:
        size -= 2
    y1 = 56 + size * .98
    y2 = y1 + size * .86
    c.put(c.text(pad, y1, P["first"], "serif", size, T["ink"], d=t0 + .25, kind="blur"))
    last, hi = P["last"], P["highlight"]
    c.put(c.runs(pad, y2, [(last[:hi], "serifi", T["ink"]), (last[hi], "serifi", T["blue"]), (last[hi + 1:], "serifi", T["ink"])],
                 size, d=t0 + .45, kind="blur"))
    # about + link
    ay = y2 + 44
    lines = wrap("sans", 16, P["about"], 330)
    for i, ln in enumerate(lines):
        c.put(c.text(pad, ay + i * 23, ln, "sans", 16, "#2a2a28", d=t0 + .85 + i * .08))
    ly = ay + len(lines) * 23 + 14
    c.put(c.text(pad, ly, P["link"], "mono", 11, T["muted"], d=t0 + 1.0))
    lw = FB.width("mono", 11, P["link"])
    c.put(f'<path{A(t0 + 1.0, "fade")} d="M{pad + lw + 9:.1f} {ly - 1} l6 -6 M{pad + lw + 9:.1f} {ly - 7} h6 v6" '
          f'fill="none" stroke="{T["muted"]}" stroke-width="1.2" stroke-linecap="round"/>')
    # halftone art
    cols = rows = 46
    pitch = 7.0
    ax0, ay0 = W - pad - cols * pitch + 8, (h - rows * pitch) / 2
    bands: dict[int, list[str]] = {}
    for col, row, dk in halftone(cols, rows):
        r = pitch * .5 * math.sqrt(max(dk, 0)) * 1.08
        if r < .55:
            continue
        cx, cy = ax0 + col * pitch + pitch / 2, ay0 + row * pitch + pitch / 2
        bands.setdefault(col * 9 // cols, []).append(
            f"M{cx - r:.1f} {cy:.1f}a{r:.2f} {r:.2f} 0 1 0 {2 * r:.2f} 0a{r:.2f} {r:.2f} 0 1 0 {-2 * r:.2f} 0")
    for b in sorted(bands):
        c.put(f'<path{A(t0 + .35 + b * .09, "fade")} d="{"".join(bands[b])}" fill="#222" fill-opacity=".82"/>')
    return c


def card_graph(S) -> Card:
    h, pad = 234, 28
    c = Card("graph", W, h, f'Every day, in blue. {fmt(S["total"])} contributions in the last year')
    c.bg(T["cream"], 16, T["line"])
    d0 = TL["graph"]
    blue = T["blue"]
    ramp = [mix(T["cream"], blue, t) for t in (.13, .32, .55, .78, 1.0)]
    c.css_extra = "".join(f".l{i}{{fill:{col}}}" for i, col in enumerate(ramp))
    c.put(c.runs(pad, 52, [("Every day, ", "serif", T["ink"]), ("in blue", "serifi", blue)], 28, d=d0))
    c.put(c.text(W - pad, 50, "REFRESHED DAILY", "mono", 9.5, T["muted"], anchor="end", ls=1.4, d=d0 + .1, kind="fade"))

    win, today = S["win"], S["today"]
    first_sun = S["start"] - dt.timedelta(days=(S["start"].weekday() + 1) % 7)
    nz = sorted(n for n in win.values() if n > 0)
    q = lambda p: nz[min(len(nz) - 1, int(p * len(nz)))] if nz else 1
    th = [q(.25), q(.5), q(.75)]
    level = lambda n: 0 if n == 0 else 1 + sum(n > t for t in th)

    pitch = (W - 2 * pad) / 53
    cell, y0 = pitch * .8, 82
    ncols = ((today - first_sun).days // 7) + 1
    legend_w = 5 * 13 + 70
    last_m, last_x = None, -99.0
    for col in range(ncols):
        cd = first_sun + dt.timedelta(days=7 * col)
        gx = pad + col * pitch
        if cd.month != last_m:
            if gx - last_x >= 42 and gx < W - pad - legend_w - 10 and cd >= S["start"] - dt.timedelta(days=6):
                c.put(c.text(gx, y0 + 7 * pitch + 22, cd.strftime("%b"), "mono", 9.5, T["muted"], d=d0 + .2 + col * .018, kind="fade"))
                last_x = gx
            last_m = cd.month
        cells = []
        for row in range(7):
            d = cd + dt.timedelta(days=row)
            if d > today:
                break
            n = win.get(d, 0) if d >= S["start"] else 0
            cells.append(f'<rect class="l{level(n)}" x="{gx:.1f}" y="{y0 + row * pitch:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2.6"/>')
        c.put(f'<g{A(d0 + .15 + col * .018, "fade")}>{"".join(cells)}</g>')
    # legend, same row as the month labels
    ly = y0 + 7 * pitch + 13
    lx = W - pad - 5 * 13 - 28
    c.put(c.text(lx - 8, ly + 9, "less", "mono", 9.5, T["muted"], anchor="end", d=d0 + 1.2, kind="fade"))
    for i in range(5):
        c.put(f'<rect{A(d0 + 1.2 + i * .05, "pop", f"l{i}")} x="{lx + i * 13}" y="{ly}" width="10" height="10" rx="2.4"/>')
    c.put(c.text(lx + 5 * 13 + 4, ly + 9, "more", "mono", 9.5, T["muted"], d=d0 + 1.2, kind="fade"))
    return c


def card_stats(S) -> Card:
    h, pad = 316, 32
    c = Card("stats", W, h, f'{fmt(S["total"])} contributions in the last 12 months')
    c.bg(T["cream"], 16, T["line"])
    d0 = TL["stats"]
    ink, mut = T["ink"], T["muted"]
    c.put(c.text(pad, 46, "THE LAST 12 MONTHS ON GITHUB", "mono", 9.5, mut, ls=1.4, d=d0))
    c.put(c.runs(pad, 108, [(fmt(S["total"]) + " ", "serif", ink), ("contributions", "serifi", ink)], 56, d=d0 + .1))

    # bar chart
    left, right, base, maxh = pad, 592, 262, 108
    months = S["months"]
    mx = max([n for _, n in months] + [1])
    slot = (right - left) / 12
    bw = slot * .64
    peak = max(range(12), key=lambda i: (months[i][1], i))
    for i, (lab, n) in enumerate(months):
        x = left + i * slot + (slot - bw) / 2
        bh = max(7, maxh * n / mx) if n else 7
        is_cur = i == 11 and S["partial"]
        col = T["grey_bar"] if is_cur else (T["blue"] if i == peak else ink)
        dd = d0 + .35 + i * .06
        c.put(f'<rect{A(dd, "grow")} x="{x:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{col}"/>')
        if i == peak and n:
            c.put(c.text(x + bw / 2, base - bh - 8, fmt(n), "mono", 9.5, T["blue"], anchor="middle", d=dd + .4, kind="fade"))
        c.put(c.text(x + bw / 2, base + 24, lab, "mono", 9, mut, anchor="middle", d=d0 + .3 + i * .03, kind="fade"))

    # right column: three stats separated by hairlines
    rx0, rx1 = 626, W - pad
    bn, bd = S["busiest"]
    streak_note = ("current streak, and the longest" if S["streak"] >= S["longest"]
                   else f'current streak, longest {S["longest"]}')
    items = [
        ([(fmt(S["streak"]) + " ", "serif", ink), ("day" + ("" if S["streak"] == 1 else "s"), "serifi", ink)], streak_note),
        ([(fmt(S["active"]), "serif", ink), (" / ", "serif", T["grey_bar"]), (fmt(S["window_days"]), "serif", ink)], "days with a commit"),
        ([(fmt(bn), "serif", ink)], f"on the busiest day, {bd:%b} {bd.day}"),
    ]
    for i, (parts, note) in enumerate(items):
        y = 92 + i * 82
        dd = d0 + .5 + i * .15
        c.put(c.runs(rx0, y, parts, 42, d=dd))
        c.put(c.text(rx0, y + 22, note, "sans", fit("sans", 11.5, note, rx1 - rx0), mut, d=dd + .08))
        if i < 2:
            c.put(f'<line{A(dd + .1, "growx")} x1="{rx0}" x2="{rx1}" y1="{y + 42}" y2="{y + 42}" stroke="{T["line"]}"/>')
    return c


def card_tile(i: int, t: dict) -> Card:
    w, h = 300, 204
    c = Card(f'tile-{t["slug"]}', w, h, f'{t["num"]} {t["word"]}. {t["desc"]}')
    bg = T[t["bg"]]
    c.bg(bg, 14)
    fg = t["fg"]
    soft = mix(fg, bg, .22)
    d0 = TL["tiles"][i]
    c.put(c.text(24, 42, t["eyebrow"], "mono", 9.5, soft, ls=1.4, d=d0 + .1, kind="fade"))
    c.put(f'<path{A(d0 + .15, "fade")} d="M{w - 40} 38h12M{w - 33} 33l5 5-5 5" fill="none" stroke="{fg}" '
          f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>')
    size = 50
    while size > 30 and FB.width("serif", size, t["num"] + " ") + FB.width("serifi", size, t["word"]) > w - 48:
        size -= 2
    c.put(c.runs(24, 116, [(t["num"] + " ", "serif", fg), (t["word"], "serifi", fg)], size, d=d0 + .2))
    for k, ln in enumerate(wrap("sans", 12.5, t["desc"], w - 48)[:3]):
        c.put(c.text(24, 152 + k * 19, ln, "sans", 12.5, fg, d=d0 + .35 + k * .08))
    return c


def card_heading(dark: bool) -> Card:
    c = Card("heading-dark" if dark else "heading-light", W, 64, "What I'm coding")
    col = "#f3f2ee" if dark else "#111111"
    c.put(c.runs(4, 44, [("What I’m ", "serif", col), ("coding", "serifi", col)], 32, d=TL["heading"]))
    return c


LANG_COLORS = {"Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "HTML": "#e34c26",
               "Jupyter Notebook": "#DA5B0B", "CSS": "#563d7c"}


def card_repo(i: int, f: dict, live: dict | None) -> Card:
    w, h = 300, 142
    c = Card(f'repo-{f["repo"].lower()}', w, h, f'{f["title"]}: {f["desc"]}')
    c.bg(T["white"], 12, T["line"])
    d0 = TL["repos"][i]
    c.put(f'<rect{A(d0 + .05, "pop")} x="22" y="25" width="7" height="7" rx="1.6" fill="{T["blue"]}"/>')
    c.put(c.text(38, 33, f["title"], "sansb", 15, T["ink"], d=d0 + .1))
    for k, ln in enumerate(wrap("sans", 12.5, f["desc"], w - 44)[:3]):
        c.put(c.text(22, 62 + k * 18, ln, "sans", 12.5, "#55534d", d=d0 + .25 + k * .08))
    lang = None
    stars = None
    if live:
        lg = live.get("language") or live.get("primaryLanguage")
        lang = lg.get("name") if isinstance(lg, dict) else lg
        stars = live.get("stars", live.get("stargazerCount"))
    # footer line: "TypeScript · ★ 20" (the star is drawn, JetBrains Mono has no such glyph)
    x, fy = 22, h - 22
    if lang:
        c.put(c.text(x, fy, lang, "mono", 10, T["muted"], d=d0 + .5, kind="fade"))
        x += FB.width("mono", 10, lang) + 8
    if stars is not None:
        if lang:
            c.put(c.text(x, fy, "·", "mono", 10, T["muted"], d=d0 + .5, kind="fade"))
            x += 14
        c.put(f'<path{A(d0 + .5, "fade")} transform="translate({x:.1f} {fy - 9.5:.1f}) scale(.66)" fill="{T["muted"]}" '
              f'd="M8 1.5l1.9 4.3 4.6.4-3.5 3 1.1 4.5L8 11.2 3.9 13.7 5 9.2 1.5 6.2l4.6-.4z"/>')
        c.put(c.text(x + 14, fy, fmt(stars), "mono", 10, T["muted"], d=d0 + .5, kind="fade"))
    return c


# ───────────────────────────── main ─────────────────────────────────────────


def preview(names: dict) -> str:
    img = lambda n, w="100%": f'<img src="{n}.svg" width="{w}">'
    return f"""<!doctype html><meta charset="utf-8"><title>Profile preview (static)</title>
<body style="margin:0;background:#0d1117;color:#e6edf7;font:16px system-ui">
<div style="max-width:830px;margin:24px auto">
<p align="center">{img(names["hero"])}</p>
<p align="center">{img(names["graph"])}</p>
<p align="center">{img(names["stats"])}</p>
<p align="center">
{chr(10).join(img(n, "32.5%") for n in names["tiles"])}
</p>
<p align="center">{img("heading-dark")}</p>
<p align="center">
{chr(10).join(img(n, "32.5%") for n in names["repos"])}
</p>
</div></body>"""


def main() -> None:
    global FB
    FB = FontBook()
    days, live = load_data()
    S = stats(days)
    cards = {
        "hero": card_hero(), "graph": card_graph(S), "stats": card_stats(S),
        "tiles": [card_tile(i, t) for i, t in enumerate(TILES)],
        "heading": [card_heading(True), card_heading(False)],
        "repos": [card_repo(i, f, live.get(f["repo"].lower())) for i, f in enumerate(FEATURED)],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    names: dict = {}
    for key, val in cards.items():
        lst = val if isinstance(val, list) else [val]
        for card in lst:
            svg = card.svg()
            (OUT / f"{card.name}.svg").write_text(svg, encoding="utf-8")
            print(f"  {OUT.name}/{card.name}.svg  {len(svg) / 1024:.1f} KB")
        names[key] = [x.name for x in lst] if isinstance(val, list) else val.name
    if STATIC:
        (OUT / "preview.html").write_text(preview(names), encoding="utf-8")
        print(f"\nSTATIC=1: open {OUT / 'preview.html'} and screenshot it. cards/ was not touched.")


if __name__ == "__main__":
    main()
