"""Render assets/brand/numbers.png: the headline benchmark totals in big type.

Totals come from benchmarks/results/token-bench.json: the dev set (rules were
tuned on it) against the unseen holdout and semantic scenarios.
    python assets/brand/numbers.py
"""

from __future__ import annotations

import json
from pathlib import Path

from render import ASH, BASE, INK, PAPER, RED, ROOT, shoot, shutil

BENCH = json.loads((ROOT / "benchmarks" / "results" / "token-bench.json").read_text(encoding="utf-8"))
ARMS = (("native", "Native"), ("rtk", "RTK"), ("jevto", "JevTO"), ("jevto-adaptive", "+ Jev"))


def totals(rows: list[dict]) -> dict:
    return {arm: [sum(r["arms"][arm][k] for r in rows) for k in ("est_tokens", "needles_found", "needles_total")]
            for arm, _ in ARMS}


def group(title: str, sub: str, rows: list[dict]) -> str:
    t = totals(rows)
    native = t["native"][0]
    bars = []
    for arm, label in ARMS:
        tokens, found, total = t[arm]
        share = tokens / native
        cut = "" if arm == "native" else f"−{round(100 * (1 - share))}%"
        facts_cls = "miss" if found < total else ""
        bars.append(f"""<div class="row {arm}"><div class="lab">{label}</div>
<div class="track"><div class="bar" style="width:{max(share * 100, 1.2):.1f}%"></div></div>
<div class="val"><b>{cut or f'{tokens:,}'}</b><span>{'' if arm == 'native' else f'{tokens:,} tok · '}<em class="{facts_cls}">{found}/{total} facts</em></span></div></div>""")
    return f'<section><h2>{title}</h2><p>{sub}</p>{"".join(bars)}</section>'


def main() -> None:
    rows = [r for r in BENCH["results"] if not r.get("skipped")]
    dev = [r for r in rows if r["suite"] == "dev"]
    unseen = [r for r in rows if r["suite"] != "dev"]
    page = BASE + f"""<style>
body {{ width: 1800px; height: 880px; background: {INK}; color: {PAPER}; overflow: hidden; }}
.w {{ padding: 70px 76px; }}
h1 {{ margin: 0 0 56px; font-size: 76px; font-weight: 900; letter-spacing: -0.04em; line-height: 1; }}
h1 span {{ color: {ASH}; }}
.g {{ display: grid; grid-template-columns: 1fr 1fr; gap: 80px; }}
h2 {{ margin: 0; font-size: 46px; font-weight: 900; letter-spacing: -0.02em; }}
p {{ margin: 6px 0 34px; font-size: 28px; color: {ASH}; font-weight: 600; }}
.row {{ display: grid; grid-template-columns: 150px 1fr; grid-template-rows: auto auto; column-gap: 20px; margin-bottom: 26px; }}
.lab {{ font-size: 32px; font-weight: 800; color: {ASH}; align-self: center; }}
.jevto .lab, .jevto-adaptive .lab {{ color: {PAPER}; }}
.track {{ height: 34px; align-self: center; }}
.bar {{ height: 100%; background: #4A4E56; border-radius: 4px; }}
.rtk .bar {{ background: #D9921E; }}
.jevto .bar, .jevto-adaptive .bar {{ background: {RED}; }}
.val {{ grid-column: 2; display: flex; align-items: baseline; gap: 16px; margin-top: 6px; }}
.val b {{ font-size: 40px; font-weight: 900; letter-spacing: -0.02em; }}
.val span {{ font-size: 26px; color: {ASH}; font-weight: 600; }}
.val em {{ font-style: normal; color: #7BD06A; }}
.val em.miss {{ color: #FF5A52; }}
</style><div class="w">
<h1>23 real commands. <span>Same hooks agents use.</span></h1>
<div class="g">{group("Dev set", "11 scenarios · rules tuned here", dev)}{group("Unseen", "12 scenarios · written after the rules froze", unseen)}</div></div>"""
    shoot(shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          page, Path(__file__).resolve().parent / "numbers.png", 1800, 880, 1, transparent=False)


if __name__ == "__main__":
    main()
