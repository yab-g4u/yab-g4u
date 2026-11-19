#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

    python scripts/render_cards.py            -> cards/*.svg          (animated)
    STATIC=1 python scripts/render_cards.py   -> cards-static/*.svg   (final frame)
                                                 + cards-static/preview.html

Everything here is built for <img> embedding, so it uses only what works there:
an inline <style> block with CSS keyframes, SMIL <animate> elements, and fonts
subset with pyftsubset and embedded as base64 WOFF data URIs. No scripts, no
external requests.

Reduced motion
  * CSS keyframes only ever define a `from` state (animation-fill-mode:
    backwards), so removing the animation leaves the element in its final,
    visible state. `prefers-reduced-motion: reduce` sets `animation: none`.
  * SMIL cannot be switched off from CSS, so it is used only for purely
    decorative loops (pulse ring, shimmer, travelling dot). Those carry the
    class `deco`, which is `display:none` under reduced motion.

Input: data/contributions.json (refreshed daily by the GitHub Action).
Optional: theme.json (colour overrides), assets/portrait.{jpg,png,webp}.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
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
W = 830  # card width; GitHub's README column is ~830px wide


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# ───────────────────────────── content ──────────────────────────────────────

PROFILE = dict(
    login="yab-g4u",
    name="Yeabsera Sisay",
    first="Yeabsera",
    last="Sisay",
    role="AI Engineer · Agentic AI · Computer Vision · AI Systems",
    about="I build intelligent systems that can see, hear, reason, and act.",
    status="Available for AI Engineering",
    place="Addis Ababa (UTC+3)",
)

# (file slug, label, sub-line, accent key, icon)
TILES = [
    ("portfolio", "Portfolio", "yeabsera-sisay.vercel.app", "brand", "globe"),
    ("linkedin", "LinkedIn", "Let's connect", "linkedin", "in"),
    ("email", "Email", "g4uforlife@gmail.com", "brand2", "mail"),
]

# Repo names must match the GitHub repo name (case-insensitive) for live stats.
FEATURED = [
    dict(repo="omniq", title="Omniq", sub="Voice AI",
         desc="Voice AI infrastructure for people without smartphones: 2G/PSTN access, "
              "realtime conversational AI, IVR and multilingual extraction.",
         tags=["Python", "Voice AI", "Realtime", "IVR", "LLMs"]),
    dict(repo="medscop", title="Medscope", sub="Multi-agent systems",
         desc="Epidemic simulation combined with specialised AI agents for policy, "
              "logistics, government response and sentiment analysis.",
         tags=["Multi-Agent AI", "Simulation", "Blockchain", "Decision Support"]),
    dict(repo="attlas", title="ATLAS", sub="Developer infrastructure",
         desc="Turns GitHub repositories and career data into deployable portfolios "
              "through AST analysis, design synthesis and one-click deploys.",
         tags=["Next.js", "GitHub API", "Design Synthesis", "AI Automation"]),
    dict(repo="IDA", title="IDA", sub="Healthcare AI",
         desc="Healthcare assistant with verified medicine discovery, clinical "
              "interaction warnings and a pharmacy stock locator.",
         tags=["Healthcare AI", "Search", "Blockchain", "Python"]),
]

LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "Jupyter Notebook": "#DA5B0B", "HTML": "#e34c26", "CSS": "#563d7c",
    "Go": "#00ADD8", "Rust": "#dea584", "Shell": "#89e051",
}

# ───────────────────────────── theme ────────────────────────────────────────
# Drop a theme.json next to this repo's README to override any key; see README.
# `surface` and `linkedin` come from your current README badges (#111827, #0A66C2).
# The rest are placeholders until pasted from your site's CSS variables.
THEME = dict(
    bg="#0b1020",        # page/background tone used to derive ramps
    surface="#111827",   # card fill
    border="#1f2a44",
    text="#e6edf7",
    muted="#8b98b0",
    brand="#6366f1",     # main brand colour (also the contribution graph ramp)
    brand2="#22d3ee",    # secondary accent
    ok="#34d399",        # the "available" dot
    linkedin="#0A66C2",
)
_theme_file = ROOT / "theme.json"
if _theme_file.exists():
    THEME.update(json.loads(_theme_file.read_text()))
T = THEME


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, t: float) -> str:
    """Blend colour a toward colour b by t (0..1)."""
    return "#%02x%02x%02x" % tuple(round(x * (1 - t) + y * t) for x, y in zip(_rgb(a), _rgb(b)))


# ───────────────────────────── fonts ────────────────────────────────────────
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
SANS = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

# role -> font spec. Variable fonts are pinned to one instance per role, then
# subset per card to just the characters that card uses.
# Swap files/URLs here to match your site's typefaces.
FONTS = {
    "display": dict(cls="fd", alias="ys-display", file="Inter-VF.ttf", url=GF + "inter/Inter%5Bopsz%2Cwght%5D.ttf",
                    axes={"wght": 800, "opsz": 32}, weight=800, stack=SANS),
    "strong": dict(cls="fs", alias="ys-strong", file="Inter-VF.ttf", url=GF + "inter/Inter%5Bopsz%2Cwght%5D.ttf",
                   axes={"wght": 600, "opsz": 14}, weight=600, stack=SANS),
    "text": dict(cls="ft", alias="ys-text", file="Inter-VF.ttf", url=GF + "inter/Inter%5Bopsz%2Cwght%5D.ttf",
                 axes={"wght": 400, "opsz": 14}, weight=400, stack=SANS),
    "mono": dict(cls="fm", alias="ys-mono", file="JetBrainsMono-VF.ttf",
                 url=GF + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
                 axes={"wght": 500}, weight=500, stack=MONO),
}


class FontBook:
    def __init__(self) -> None:
        self.tt: dict[str, object] = {}
        self.path: dict[str, Path] = {}
        self.cmap: dict[str, dict] = {}
        self.upem: dict[str, int] = {}
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
                    warn(f"could not fetch {spec['file']} ({e}); '{role}' falls back to system font")
                    continue
            font = TTFont(src)
            if "fvar" in font:
                limits = {}
                for ax in font["fvar"].axes:
                    if ax.axisTag in spec["axes"]:
                        limits[ax.axisTag] = max(ax.minValue, min(ax.maxValue, spec["axes"][ax.axisTag]))
                font = instancer.instantiateVariableFont(font, limits)
            p = self.tmp / f"inst-{role}.ttf"
            font.save(p)
            self.path[role] = p
            f2 = TTFont(p)
            self.tt[role] = f2
            self.cmap[role] = f2.getBestCmap()
            self.upem[role] = f2["head"].unitsPerEm

    def embedded(self, role: str) -> bool:
        return role in self.tt

    def width(self, role: str, size: float, s: str, ls: float = 0) -> float:
        if role not in self.tt:
            return len(s) * size * (0.62 if role == "mono" else 0.56) + ls * len(s)
        hm = self.tt[role]["hmtx"]
        cm = self.cmap[role]
        total = 0
        for ch in s:
            g = cm.get(ord(ch))
            total += hm[g][0] if g else self.upem[role] * 0.55
        return total / self.upem[role] * size + ls * len(s)

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


FB: FontBook  # set in main()


def fit(role: str, size: float, s: str, maxw: float, lo: float = 11, ls: float = 0) -> float:
    while size > lo and FB.width(role, size, s, ls) > maxw:
        size -= 0.5
    return size


def wrap(role: str, size: float, s: str, maxw: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and FB.width(role, size, trial) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


# ───────────────────────────── card plumbing ────────────────────────────────

CSS_ANIM = """
@keyframes rise{from{opacity:0;transform:translateY(12px)}}
@keyframes fade{from{opacity:0}}
@keyframes pop{from{opacity:0;transform:scale(.4)}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
@keyframes draw{from{stroke-dashoffset:1}}
.a{animation-duration:.8s;animation-timing-function:cubic-bezier(.2,.7,.2,1);animation-fill-mode:backwards;animation-delay:var(--d,0s)}
.rise{animation-name:rise}.fade{animation-name:fade}
.pop{animation-name:pop;transform-box:fill-box;transform-origin:center}
.grow{animation-name:grow;transform-box:fill-box;transform-origin:50% 100%}
.growx{animation-name:growx;transform-box:fill-box;transform-origin:0 50%}
.draw{animation-name:draw;animation-duration:1.6s}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}.deco{display:none}}
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def A(d: float | None, kind: str = "rise", cls: str = "") -> str:
    """Class + stagger delay attributes for a non-text element (empty in STATIC)."""
    if not ANIM or d is None:
        return f' class="{cls}"' if cls else ""
    return f' class="{(cls + " ").lstrip()}a {kind}" style="--d:{d:.2f}s"'


class Card:
    def __init__(self, name: str, w: int, h: int, label: str):
        self.name, self.w, self.h, self.label = name, w, h, label
        self.defs: list[str] = []
        self.body: list[str] = []
        self.chars: dict[str, set[str]] = {}

    def put(self, *parts: str) -> None:
        self.body.extend(parts)

    def text(self, x, y, s, role, size, fill, anchor="start", ls=0, d=None, kind="rise", extra=""):
        self.chars.setdefault(role, set()).update(s)
        cls = FONTS[role]["cls"]
        st = ""
        if ANIM and d is not None:
            cls += f" a {kind}"
            st = f' style="--d:{d:.2f}s"'
        anch = "" if anchor == "start" else f' text-anchor="{anchor}"'
        lsa = f' letter-spacing="{ls}"' if ls else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-size="{size}" fill="{fill}"'
                f'{anch}{lsa}{st}{extra}>{esc(s)}</text>')

    def svg(self) -> str:
        css = ["text{text-rendering:geometricPrecision}"]
        for role in self.chars:
            spec = FONTS[role]
            face = FB.face(role, self.chars[role])
            if face:
                css.append(face)
                css.append(f'.{spec["cls"]}{{font-family:"{spec["alias"]}",{spec["stack"]}}}')
            else:  # fallback: real weight on the system font
                css.append(f'.{spec["cls"]}{{font-family:{spec["stack"]};font-weight:{spec["weight"]}}}')
        css.append(self.css_extra if hasattr(self, "css_extra") else "")
        if ANIM:
            css.append(CSS_ANIM)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
            f'<title>{esc(self.label)}</title><style>{"".join(css)}</style>'
            f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>'
        )


def card_bg(c: Card, rx=18) -> None:
    c.defs.append(f'<clipPath id="clip"><rect width="{c.w}" height="{c.h}" rx="{rx}"/></clipPath>')
    c.put(f'<rect x=".5" y=".5" width="{c.w - 1}" height="{c.h - 1}" rx="{rx}" fill="{T["surface"]}" stroke="{T["border"]}"/>')


def grad(c: Card) -> None:
    c.defs.append(
        f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{T["brand"]}"/>'
        f'<stop offset="1" stop-color="{T["brand2"]}"/></linearGradient>')


# timeline (seconds): the page plays top to bottom
TL = dict(hero=0.0, tiles=[0.55, 0.70, 0.85], chart=1.25, repos=[2.05, 2.40, 2.75, 3.10, 3.45], graph=3.9)

# ───────────────────────────── data ─────────────────────────────────────────


def load_data():
    raw = json.loads(DATA.read_text())
    days: dict[str, int] = {}
    if "days" in raw:
        days = {d["date"]: int(d.get("count", d.get("contributionCount", 0))) for d in raw["days"]}
    else:
        cal = raw
        if "data" in cal:
            cal = cal["data"]
        if "user" in cal:
            cal = cal["user"]["contributionsCollection"]["contributionCalendar"]
        elif "contributionCalendar" in cal:
            cal = cal["contributionCalendar"]
        for wk in cal["weeks"]:
            for d in wk["contributionDays"]:
                days[d["date"]] = int(d["contributionCount"])
    repos = {r["name"].lower(): r for r in raw.get("repos", [])}
    if not days:
        raise SystemExit(f"no contribution days found in {DATA}")
    return {dt.date.fromisoformat(k): v for k, v in days.items()}, repos


def stats(days: dict[dt.date, int]):
    today = max(days)
    start = today - dt.timedelta(days=364)
    win = {d: n for d, n in days.items() if start <= d <= today}
    total = sum(win.values())
    active = sum(1 for n in win.values() if n > 0)
    # current streak: today may still be empty, so count back from yesterday then
    d = today if win.get(today, 0) > 0 else today - dt.timedelta(days=1)
    streak = 0
    while win.get(d, 0) > 0:
        streak += 1
        d -= dt.timedelta(days=1)
    best_n, best_d = max(((n, d) for d, n in win.items()), key=lambda t: (t[0], t[1]))
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    per_month = [(dt.date(y, m, 1).strftime("%b"), sum(n for d, n in win.items() if (d.year, d.month) == (y, m)))
                 for y, m in months]
    return dict(today=today, start=start, win=win, total=total, active=active, streak=streak,
                busiest=(best_n, best_d), months=per_month, window_days=len(win))


# ───────────────────────────── cards ────────────────────────────────────────


def find_portrait() -> str | None:
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = ROOT / "assets" / f"portrait.{ext}"
        if p.exists():
            try:
                from PIL import Image, ImageOps
                im = ImageOps.fit(Image.open(p).convert("RGB"), (320, 320))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=84, optimize=True)
                return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            except ImportError:
                if p.stat().st_size < 120_000:
                    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
                warn("portrait is large and Pillow is missing (pip install pillow); skipping it")
    return None


def card_hero(portrait: str | None) -> Card:
    h, pad = 310, 48
    c = Card("hero", W, h, f'{PROFILE["name"]}, {PROFILE["role"]}')
    grad(c)
    card_bg(c)
    t0 = TL["hero"]
    avail = W - pad - 250
    c.defs.append(
        f'<radialGradient id="gl"><stop offset="0" stop-color="{T["brand"]}" stop-opacity=".38"/>'
        f'<stop offset="1" stop-color="{T["brand"]}" stop-opacity="0"/></radialGradient>'
        f'<pattern id="grid" width="28" height="28" patternUnits="userSpaceOnUse">'
        f'<path d="M28 0H0V28" fill="none" stroke="{T["border"]}" stroke-width="1" opacity=".55"/></pattern>'
        f'<linearGradient id="sh" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="#fff" stop-opacity=".05"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>')
    shimmer = ""
    if ANIM:
        shimmer = (f'<rect class="deco" x="-220" y="0" width="180" height="{h}" fill="url(#sh)" transform="skewX(-18)">'
                   f'<animateTransform attributeName="transform" type="translate" additive="sum" from="0 0" to="{W + 460} 0" '
                   f'dur="7s" begin="2.5s" repeatCount="indefinite"/></rect>')
    c.put(f'<g clip-path="url(#clip)"><rect width="{W}" height="{h}" fill="url(#grid)" opacity=".5"/>'
          f'<circle cx="{W - 120}" cy="40" r="300" fill="url(#gl)"/>{shimmer}</g>')

    # status chip
    chip = c.text(pad + 32, 57, PROFILE["status"], "mono", 12, T["text"])
    cw = FB.width("mono", 12, PROFILE["status"]) + 52
    ring = ""
    if ANIM:
        ring = (f'<circle class="deco" cx="{pad + 18}" cy="52" r="4" fill="none" stroke="{T["ok"]}" stroke-width="1.5">'
                f'<animate attributeName="r" values="4;12" dur="2.4s" begin="1s" repeatCount="indefinite"/>'
                f'<animate attributeName="opacity" values=".8;0" dur="2.4s" begin="1s" repeatCount="indefinite"/></circle>')
    c.put(f'<g{A(t0 + 0.10)}><rect x="{pad}" y="36" width="{cw:.1f}" height="32" rx="16" '
          f'fill="{mix(T["ok"], T["surface"], .88)}" stroke="{mix(T["ok"], T["surface"], .6)}"/>'
          f'<circle cx="{pad + 18}" cy="52" r="4" fill="{T["ok"]}"/>{ring}{chip}</g>')

    # name
    size = 64
    total = lambda s_: FB.width("display", s_, PROFILE["first"] + " " + PROFILE["last"])
    while size > 34 and total(size) > avail:
        size -= 1
    ny = 150
    c.put(c.text(pad, ny, PROFILE["first"], "display", size, T["text"], d=t0 + 0.25))
    x2 = pad + FB.width("display", size, PROFILE["first"] + " ")
    c.put(c.text(x2, ny, PROFILE["last"], "display", size, "url(#g)", d=t0 + 0.40))
    c.put(f'<rect{A(t0 + 0.85, "growx")} x="{pad}" y="{ny + 18}" width="72" height="4" rx="2" fill="url(#g)"/>')

    rs = fit("strong", 18, PROFILE["role"], avail)
    c.put(c.text(pad, ny + 58, PROFILE["role"], "strong", rs, T["brand2"], d=t0 + 0.60))
    ab = fit("text", 18, PROFILE["about"], avail, lo=13)
    c.put(c.text(pad, ny + 90, PROFILE["about"], "text", ab, mix(T["text"], T["muted"], .35), d=t0 + 0.80))
    c.put(c.text(pad, h - 26, PROFILE["place"], "mono", 12, T["muted"], d=t0 + 1.0))

    # right side: portrait, or a see/hear/reason/act motif
    if portrait:
        r, cx, cy = 86, W - pad - 96, h / 2
        c.defs.append(f'<clipPath id="pf"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>')
        c.put(f'<g{A(t0 + 0.30, "pop")}><image href="{portrait}" x="{cx - r}" y="{cy - r}" width="{2 * r}" '
              f'height="{2 * r}" clip-path="url(#pf)" preserveAspectRatio="xMidYMid slice"/></g>',
              f'<circle{A(t0 + 0.50, "draw")} cx="{cx}" cy="{cy}" r="{r + 10}" fill="none" stroke="url(#g)" '
              f'stroke-width="3" stroke-linecap="round" pathLength="1" stroke-dasharray="1" '
              f'transform="rotate(-90 {cx} {cy})"/>')
    else:
        nx, ys = W - 190, [84, 144, 204, 264]
        c.put(f'<path{A(t0 + 0.4, "draw")} d="M{nx} {ys[0]}V{ys[-1]}" stroke="{T["brand"]}" stroke-opacity=".55" '
              f'stroke-width="2" fill="none" pathLength="1" stroke-dasharray="1"/>')
        for i, (lab, y) in enumerate(zip(["see", "hear", "reason", "act"], ys)):
            dd = t0 + 0.55 + i * 0.18
            c.put(f'<circle{A(dd, "pop")} cx="{nx}" cy="{y}" r="7" fill="{T["surface"]}" stroke="url(#g)" stroke-width="2.5"/>',
                  c.text(nx + 22, y + 5, lab, "mono", 15, T["text"], d=dd + 0.05))
        if ANIM:
            c.put(f'<circle class="deco" r="3.5" fill="{T["brand2"]}"><animateMotion dur="3.6s" begin="1.6s" '
                  f'repeatCount="indefinite" path="M{nx} {ys[0]}V{ys[-1]}"/></circle>')
    return c


def _icon(c: Card, kind: str, x: float, y: float, col: str, d: float) -> str:
    if kind == "globe":
        g = (f'<circle cx="0" cy="0" r="10" fill="none" stroke="{col}" stroke-width="1.8"/>'
             f'<ellipse cx="0" cy="0" rx="4.2" ry="10" fill="none" stroke="{col}" stroke-width="1.8"/>'
             f'<path d="M-10 0H10" stroke="{col}" stroke-width="1.8"/>')
    elif kind == "mail":
        g = (f'<rect x="-11" y="-8" width="22" height="16" rx="3" fill="none" stroke="{col}" stroke-width="1.8"/>'
             f'<path d="M-10 -6L0 2L10 -6" fill="none" stroke="{col}" stroke-width="1.8" stroke-linejoin="round"/>')
    else:
        return c.text(x, y + 6, "in", "display", 18, col, anchor="middle", d=d)
    return f'<g transform="translate({x} {y})">{g}</g>'


def card_tile(i: int, slug, label, sub, accent_key, icon) -> Card:
    w, h = 300, 116
    acc = T[accent_key]
    c = Card(f"tile-{slug}", w, h, f"{label}: {sub}")
    card_bg(c, 16)
    d0 = TL["tiles"][i]
    c.put(f'<rect{A(d0 + .25, "growx")} x="20" y="1" width="{w - 40}" height="2" rx="1" fill="{acc}"/>')
    c.put(f'<g{A(d0, "pop")}><rect x="20" y="34" width="48" height="48" rx="12" fill="{mix(acc, T["surface"], .84)}" '
          f'stroke="{mix(acc, T["surface"], .55)}"/></g>')
    c.put(f'<g{A(d0 + .1, "fade")}>{_icon(c, icon, 44, 58, acc, d0 + .1)}</g>')
    c.put(c.text(84, 55, label, "strong", 19, T["text"], d=d0 + .12))
    sz = fit("mono", 12, sub, w - 84 - 40)
    c.put(c.text(84, 77, sub, "mono", sz, T["muted"], d=d0 + .22))
    c.put(f'<path{A(d0 + .3, "fade")} d="M{w - 30} 52l7 7-7 7" fill="none" stroke="{T["muted"]}" stroke-width="2" '
          f'stroke-linecap="round" stroke-linejoin="round"/>')
    return c


def card_chart(S) -> Card:
    h, pad = 336, 48
    c = Card("chart", W, h, f'Contributions per month, {fmt(S["total"])} in the last year')
    grad(c)
    card_bg(c)
    c.defs.append(
        f'<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{T["brand"]}"/>'
        f'<stop offset="1" stop-color="{mix(T["brand"], T["surface"], .65)}"/></linearGradient>'
        f'<linearGradient id="barmax" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{T["brand2"]}"/>'
        f'<stop offset="1" stop-color="{T["brand"]}"/></linearGradient>')
    d0 = TL["chart"]
    c.put(c.text(pad, 42, "Contributions per month", "strong", 17, T["text"], d=d0))
    c.put(c.text(W - pad, 42, f'last 12 months · {fmt(S["total"])} total', "mono", 12, T["muted"], anchor="end", d=d0 + .05))

    bn, bd = S["busiest"]
    blocks = [
        ("CURRENT STREAK", fmt(S["streak"]), "day" + ("" if S["streak"] == 1 else "s"), "keep it going" if S["streak"] else "starts with one commit"),
        ("ACTIVE DAYS", fmt(S["active"]), f'/ {S["window_days"]}', f'{round(100 * S["active"] / max(1, S["window_days"]))}% of the year'),
        ("BUSIEST DAY", fmt(bn), "contributions", f"{bd:%b} {bd.day}, {bd.year}"),
    ]
    for i, (lab, val, unit, note) in enumerate(blocks):
        x = pad + i * 250
        dd = d0 + .15 + i * .12
        c.put(c.text(x, 84, lab, "mono", 11, T["muted"], ls=1.2, d=dd))
        c.put(c.text(x, 124, val, "display", 36, T["text"], d=dd + .05))
        vw = FB.width("display", 36, val)
        c.put(c.text(x + vw + 8, 124, unit, "strong", 14, T["brand2"], d=dd + .08))
        c.put(c.text(x, 147, note, "mono", 12, T["muted"], d=dd + .12))

    top, bot = 180, 292
    ch = bot - top
    months = S["months"]
    mx = max([n for _, n in months] + [1])
    slot = (W - 2 * pad) / 12
    bw = slot * .6
    for k in (0, .5, 1):
        y = bot - ch * k
        c.put(f'<line x1="{pad}" x2="{W - pad}" y1="{y:.1f}" y2="{y:.1f}" stroke="{T["border"]}" '
              f'stroke-dasharray="{"0" if k == 0 else "3 5"}" opacity="{".9" if k == 0 else ".6"}"/>')
    top_i = max(range(12), key=lambda i: months[i][1])
    for i, (lab, n) in enumerate(months):
        x = pad + i * slot + (slot - bw) / 2
        bh = max(3, ch * n / mx) if n else 3
        dd = d0 + .5 + i * .06
        fillid = "barmax" if i == top_i and n else "bar"
        c.put(f'<rect{A(dd, "grow")} x="{x:.1f}" y="{bot - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="6" '
              f'fill="url(#{fillid})" opacity="{1 if n else .35}"/>')
        if n:
            c.put(c.text(x + bw / 2, bot - bh - 8, fmt(n), "mono", 11,
                         T["brand2"] if i == top_i else T["muted"], anchor="middle", d=dd + .35, kind="fade"))
        c.put(c.text(x + bw / 2, bot + 24, lab, "mono", 12, T["muted"], anchor="middle", d=d0 + .4 + i * .03, kind="fade"))
    return c


def _lang_color(name: str) -> str:
    return LANG_COLORS.get(name, T["brand2"])


def card_repo(i: int, f: dict, live: dict | None) -> Card:
    h, pad = 156, 28
    c = Card(f'repo-{f["repo"].lower()}', W, h, f'{f["title"]}: {f["desc"]}')
    card_bg(c, 16)
    d0 = TL["repos"][i]
    c.put(f'<rect{A(d0, "grow")} x="1" y="22" width="4" height="{h - 44}" rx="2" fill="{T["brand"]}"/>')
    c.put(c.text(pad + 6, 44, f["title"], "strong", 21, T["text"], d=d0 + .08))
    tw = FB.width("strong", 21, f["title"])
    c.put(c.text(pad + 6 + tw + 12, 44, f["sub"], "mono", 12, T["muted"], d=d0 + .14))

    # right-aligned live stats, laid out right to left
    x = W - pad
    items = []
    if live:
        lang = (live.get("language") or live.get("primaryLanguage") or {})
        lang = lang.get("name") if isinstance(lang, dict) else lang
        if live.get("forks") is not None or live.get("forkCount") is not None:
            items.append(("fork", fmt(live.get("forks", live.get("forkCount", 0)))))
        if live.get("stars") is not None or live.get("stargazerCount") is not None:
            items.append(("star", fmt(live.get("stars", live.get("stargazerCount", 0)))))
        if lang:
            items.append(("lang", lang))
    for kind, val in items:
        wv = FB.width("mono", 12, val)
        dd = d0 + .2
        c.put(c.text(x, 44, val, "mono", 12, T["muted"], anchor="end", d=dd, kind="fade"))
        ix = x - wv - 8
        if kind == "star":
            c.put(f'<path{A(dd, "fade")} transform="translate({ix - 13:.1f} 31) scale(.8)" fill="{T["brand2"]}" '
                  f'd="M8 1.5l1.9 4.3 4.6.4-3.5 3 1.1 4.5L8 11.2 3.9 13.7 5 9.2 1.5 6.2l4.6-.4z"/>')
        elif kind == "fork":
            c.put(f'<g{A(dd, "fade")} transform="translate({ix - 13:.1f} 31) scale(.8)" fill="none" stroke="{T["brand2"]}" stroke-width="1.6">'
                  f'<circle cx="4" cy="3" r="2"/><circle cx="12" cy="3" r="2"/><circle cx="8" cy="13" r="2"/>'
                  f'<path d="M4 5v1.5a2 2 0 002 2h4a2 2 0 002-2V5M8 8.5V11"/></g>')
        else:
            c.put(f'<circle{A(dd, "pop")} cx="{ix - 6:.1f}" cy="40" r="5" fill="{_lang_color(val)}"/>')
            ix -= 6
        x = ix - 24
    # description
    lines = wrap("text", 14.5, f["desc"], W - 2 * pad - 8)[:2]
    for k, ln in enumerate(lines):
        c.put(c.text(pad + 6, 76 + k * 22, ln, "text", 14.5, mix(T["text"], T["muted"], .35), d=d0 + .25 + k * .08))
    # tags
    tx = pad + 6
    for k, tag in enumerate(f["tags"]):
        tw = FB.width("mono", 11, tag) + 20
        if tx + tw > W - pad:
            break
        dd = d0 + .45 + k * .07
        c.put(f'<g{A(dd, "pop")}><rect x="{tx:.1f}" y="114" width="{tw:.1f}" height="24" rx="12" '
              f'fill="{mix(T["brand"], T["surface"], .86)}" stroke="{mix(T["brand"], T["surface"], .6)}"/></g>')
        c.put(c.text(tx + tw / 2, 130, tag, "mono", 11, mix(T["brand"], T["text"], .55), anchor="middle", d=dd + .04, kind="fade"))
        tx += tw + 8
    return c


def card_graph(S) -> Card:
    pitch, cell, x0, y0 = 14, 11, 64, 82
    h = 236
    c = Card("graph", W, h, f'Contribution graph: {fmt(S["total"])} contributions in the last year')
    card_bg(c)
    d0 = TL["graph"]
    brand = T["brand"]
    empty = mix(T["border"], T["surface"], .35)
    ramp = [empty] + [mix(T["surface"], brand, t) for t in (.32, .55, .78, 1.0)]
    c.css_extra = "".join(f".l{i}{{fill:{col}}}" for i, col in enumerate(ramp))
    c.put(c.text(48, 42, "Contribution graph", "strong", 17, T["text"], d=d0))
    c.put(c.text(W - 48, 42, f'{fmt(S["total"])} contributions in the last year', "mono", 12, T["muted"], anchor="end", d=d0 + .05))

    win, today = S["win"], S["today"]
    start = S["start"]
    start -= dt.timedelta(days=(start.weekday() + 1) % 7)  # back to Sunday
    nz = sorted(n for n in win.values() if n > 0)
    q = lambda p: nz[min(len(nz) - 1, int(p * len(nz)))] if nz else 1
    th = [q(.25), q(.5), q(.75)]
    level = lambda n: 0 if n == 0 else 1 + sum(n > t for t in th)

    ncols = ((today - start).days // 7) + 1
    last_lab, last_x = None, -99
    for col in range(ncols):
        cd = start + dt.timedelta(days=7 * col)
        gx = x0 + col * pitch
        if cd.month != last_lab and gx - last_x >= 36 and cd >= S["start"] - dt.timedelta(days=6):
            c.put(c.text(gx, y0 - 10, cd.strftime("%b"), "mono", 11, T["muted"], d=d0 + .1 + col * .02, kind="fade"))
            last_lab, last_x = cd.month, gx
        elif cd.month != last_lab:
            last_lab = cd.month
        cells = []
        for row in range(7):
            d = cd + dt.timedelta(days=row)
            if d > today:
                break
            n = win.get(d, 0) if d >= S["start"] else 0
            cells.append(f'<rect class="l{level(n)}" x="{gx}" y="{y0 + row * pitch}" width="{cell}" height="{cell}" rx="2.5"/>')
        c.put(f'<g{A(d0 + .15 + col * .02, "rise")}>{"".join(cells)}</g>')
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.put(c.text(48, y0 + row * pitch + 9, lab, "mono", 10, T["muted"], d=d0 + .1, kind="fade"))
    # legend
    ly = y0 + 7 * pitch + 22
    lx = W - 48 - (5 * pitch + 2)
    c.put(c.text(lx - 10, ly + 9, "Less", "mono", 11, T["muted"], anchor="end", d=d0 + 1.2, kind="fade"))
    for i in range(5):
        c.put(f'<rect{A(d0 + 1.2 + i * .05, "pop", f"l{i}")} x="{lx + i * pitch}" y="{ly}" width="{cell}" height="{cell}" rx="2.5"/>')
    c.put(c.text(lx + 5 * pitch + 8, ly + 9, "More", "mono", 11, T["muted"], d=d0 + 1.2, kind="fade"))
    return c


def fmt(n) -> str:
    return f"{int(n):,}"


# ───────────────────────────── main ─────────────────────────────────────────


def preview(files: dict[str, list[str]]) -> str:
    imgs = lambda names, w="100%": "\n".join(f'<img src="{n}.svg" width="{w}">' for n in names)
    return f"""<!doctype html><meta charset="utf-8"><title>Profile preview (static)</title>
<body style="margin:0;background:#0d1117;color:#e6edf7;font:16px system-ui">
<div style="max-width:830px;margin:24px auto">
<p align="center">{imgs(files["hero"])}</p>
<p align="center">{imgs(files["tiles"], "32.5%")}</p>
<p align="center">{imgs(files["chart"])}</p>
<p align="center">{imgs(files["repos"])}</p>
<p align="center">{imgs(files["graph"])}</p>
</div></body>"""


def main() -> None:
    global FB
    FB = FontBook()
    days, live = load_data()
    S = stats(days)
    portrait = find_portrait()

    cards: dict[str, list[Card]] = {
        "hero": [card_hero(portrait)],
        "tiles": [card_tile(i, *t) for i, t in enumerate(TILES)],
        "chart": [card_chart(S)],
        "repos": [card_repo(i, f, live.get(f["repo"].lower())) for i, f in enumerate(FEATURED)],
        "graph": [card_graph(S)],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    names: dict[str, list[str]] = {}
    for group, lst in cards.items():
        names[group] = []
        for card in lst:
            svg = card.svg()
            (OUT / f"{card.name}.svg").write_text(svg, encoding="utf-8")
            names[group].append(card.name)
            print(f"  {OUT.name}/{card.name}.svg  {len(svg) / 1024:.1f} KB")
    if STATIC:
        (OUT / "preview.html").write_text(preview(names), encoding="utf-8")
        print(f"\nSTATIC=1: open {OUT / 'preview.html'} and screenshot it. Nothing in cards/ was touched.")


if __name__ == "__main__":
    main()
