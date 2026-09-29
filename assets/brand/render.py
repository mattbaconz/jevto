"""Render JevTO brand PNGs (lockups, icons, social card) from HTML.

Needs a local Chrome or Edge for headless screenshots; no Python packages.
    python assets/brand/render.py [--chrome PATH]
Sources: jevto-mark.svg, jevto-mark-dark.svg, site/fonts/Chivo.ttf (OFL).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

BRAND = Path(__file__).resolve().parent
ROOT = BRAND.parents[1]
FONT = (ROOT / "site" / "fonts" / "Chivo.ttf").as_uri()
MARK = (BRAND / "jevto-mark.svg").as_uri()
MARK_DARK = (BRAND / "jevto-mark-dark.svg").as_uri()
INK, PAPER, RED, ASH = "#111214", "#F7F5F0", "#E5231B", "#8B8F97"

BASE = f"""<!doctype html><meta charset="utf-8"><style>
@font-face {{ font-family: Chivo; src: url("{FONT}"); font-weight: 100 900; }}
html, body {{ margin: 0; background: transparent; }}
body {{ font-family: Chivo, sans-serif; }}
</style>"""


def lockup(mark: str, color: str) -> str:
    return BASE + f"""<style>
.l {{ display: flex; align-items: center; gap: 22px; padding: 8px 10px; width: max-content; }}
.w {{ font-size: 92px; font-weight: 800; letter-spacing: -0.035em; line-height: 1; color: {color}; transform: translateY(-4px); }}
</style><div class="l"><img src="{mark}" width="108" height="108"><span class="w">jevto</span></div>"""


def icon(size: int) -> str:
    return BASE + f'<img src="{MARK}" width="{size}" height="{size}" style="display:block">'


POSTER_CSS = f"""<style>
body {{ background: {INK}; color: {PAPER}; overflow: hidden; }}
.p {{ position: absolute; inset: 0; }}
.brand {{ display: flex; align-items: center; }}
.brand b {{ font-weight: 900; letter-spacing: -0.045em; line-height: .8; }}
.say {{ font-weight: 900; letter-spacing: -0.035em; line-height: .98; }}
.say span {{ color: {ASH}; }}
.say i {{ font-style: normal; color: {RED}; }}
.out {{ font-family: Consolas, "Cascadia Mono", monospace; font-weight: 700; letter-spacing: -0.01em; }}
.out div {{ white-space: nowrap; }}
.out .noise {{ color: #4A4E56; text-decoration: line-through; text-decoration-thickness: 3px; }}
.out .gap {{ color: {ASH}; }}
.out .fact {{ color: #fff; background: {RED}; display: inline-block; padding: 0 .3em; }}
.num {{ font-weight: 900; letter-spacing: -0.05em; line-height: .82; }}
.lab {{ font-weight: 700; color: {ASH}; line-height: 1.1; }}
</style>"""

OUTPUT_LINES = """<div class="noise">test tests::case_147 ... ok</div>
<div class="noise">test tests::case_148 ... ok</div>
<div class="gap">... 150 passing tests hidden</div>
<div class="fact">leading space must be rejected</div>"""


def banner() -> str:
    """README header: brand, the promise in caveman, and the idea drawn as output."""
    return BASE + POSTER_CSS + f"""<style>body {{ width: 1600px; height: 600px; }}</style><div class="p">
<div class="brand" style="position:absolute;left:90px;top:84px;gap:34px"><img src="{MARK_DARK}" width="150" height="150"><b style="font-size:176px">jevto</b></div>
<div class="say" style="position:absolute;left:92px;top:318px;font-size:84px">Agent read fact.<br><span>Noise go in <i>cave</i>.</span></div>
<div class="out" style="position:absolute;right:90px;top:112px;font-size:34px;line-height:1.75;text-align:left">{OUTPUT_LINES}</div>
<div class="lab" style="position:absolute;right:90px;bottom:72px;font-size:30px;text-align:right">−94% output · 45/46 facts kept<br>anything hidden comes back byte for byte</div>
</div>"""


def social() -> str:
    """Link preview card: one big number, one big sentence."""
    return BASE + POSTER_CSS + f"""<style>body {{ width: 1200px; height: 630px; }}</style><div class="p">
<div class="brand" style="position:absolute;left:72px;top:62px;gap:20px"><img src="{MARK_DARK}" width="84" height="84"><b style="font-size:96px">jevto</b></div>
<div class="say" style="position:absolute;left:72px;top:210px;font-size:78px">Agent read fact.<br><span>Noise go in <i>cave</i>.</span></div>
<div style="position:absolute;left:72px;bottom:62px;display:flex;align-items:flex-end;gap:26px">
  <div class="num" style="font-size:150px">−94%</div>
  <div class="lab" style="font-size:34px;padding-bottom:6px">less command output<br><span style="color:{PAPER}">45 of 46 facts kept</span></div>
</div>
</div>"""


def shoot(chrome: str, html: str, out: Path, width: int, height: int, scale: int = 1, transparent: bool = True) -> None:
    with tempfile.TemporaryDirectory() as temp:
        page = Path(temp) / "page.html"
        page.write_text(html, encoding="utf-8")
        args = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                "--force-color-profile=srgb", f"--force-device-scale-factor={scale}", f"--window-size={width},{height}",
                f"--screenshot={out}", page.as_uri()]
        if transparent:
            args.insert(1, "--default-background-color=00000000")
        subprocess.run(args, check=True, capture_output=True, timeout=60)
    print(f"wrote {out.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", default=shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    chrome = parser.parse_args().chrome
    shoot(chrome, lockup(MARK, INK), BRAND / "jevto-lockup-light.png", 420, 124, 2)
    shoot(chrome, lockup(MARK_DARK, PAPER), BRAND / "jevto-lockup-dark.png", 420, 124, 2)
    for size in (64, 256):
        shoot(chrome, icon(size), ROOT / "site" / "assets" / f"jevto-mark-{size}.png", size, size)
    shoot(chrome, social(), ROOT / "site" / "assets" / "og.png", 1200, 630, 1, transparent=False)
    shoot(chrome, banner(), BRAND / "jevto-banner.png", 1600, 600, 1, transparent=False)


if __name__ == "__main__":
    main()
