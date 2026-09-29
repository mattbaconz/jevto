"""Render assets/brand/compare-git-log.png from the committed benchmark views.

Three columns, one per arm, for `git log` over 260 commits with the goal
"When did we change the reconnect backoff?". Token counts and verdicts come
from benchmarks/results/token-bench.json; terminal lines are real lines from
benchmarks/results/views/git-log-history, shortened for display.
    python assets/brand/compare.py
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from render import ASH, BASE, INK, PAPER, RED, ROOT, shoot, shutil

VIEWS = ROOT / "benchmarks" / "results" / "views" / "git-log-history"
NEEDLE = "Increase reconnect backoff to 250ms"
BENCH = json.loads((ROOT / "benchmarks" / "results" / "token-bench.json").read_text(encoding="utf-8"))
ROW = next(r for r in BENCH["results"] if r["name"] == "git-log-history")


def lines(arm: str) -> list[str]:
    text = (VIEWS / f"{arm}.txt").read_text(encoding="utf-8").replace("'<jevto>'", "jevto")
    return [line.rstrip() for line in text.splitlines()]


def column(title: str, arm: str, shown: list[tuple[str, str]], more: str) -> str:
    data = ROW["arms"][arm]
    ok = data["needles_found"] == data["needles_total"]
    rows = "".join(f'<div class="{cls}">{html.escape(text) or "&nbsp;"}</div>' for cls, text in shown)
    verdict = '<div class="v ok">✓ has the answer</div>' if ok else '<div class="v no">✗ answer gone</div>'
    return f"""<section class="{arm}"><h2>{title}</h2>
<div class="n">{data['est_tokens']:,}<small>tokens</small></div>{verdict}
<div class="t">{rows}<div class="more">{more}</div></div></section>"""


def main() -> None:
    native, rtk, jevto = lines("native"), lines("rtk"), lines("jevto")
    hit = next(line for line in jevto if NEEDLE in line)
    gaps = [line for line in jevto if line.startswith("... ") and "hidden [" in line]
    columns = [
        column("Native", "native", [("", l) for l in native[:5]], f"+ {len(native) - 5:,} more lines"),
        column("RTK", "rtk", [("", l) for l in rtk[:5]], f"+ {len(rtk) - 5} more · newest 10 commits only"),
        column("JevTO", "jevto", [("", jevto[0]), ("gap", gaps[0].split(" [")[0]), ("hit", hit.strip()),
                                   ("gap", gaps[1].split(" [")[0]), ("gap", jevto[-1].split(" [")[0])],
               f"{len(jevto)} lines · every gap recallable"),
    ]
    page = BASE + f"""<style>
body {{ width: 1800px; height: 820px; background: {INK}; color: {PAPER}; overflow: hidden; }}
.w {{ padding: 70px 76px; }}
h1 {{ margin: 0; font-size: 78px; font-weight: 900; letter-spacing: -0.04em; line-height: 1; }}
h1 code {{ font-family: Consolas, monospace; font-weight: 700; letter-spacing: -0.02em; background: #22252A; padding: 0 .15em; border-radius: 10px; }}
p {{ margin: 20px 0 54px; font-size: 34px; color: {ASH}; font-weight: 600; }}
p b {{ color: {PAPER}; }}
.g {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 34px; }}
section {{ border-top: 8px solid #3A3D44; padding-top: 26px; min-width: 0; }}
section.jevto {{ border-top-color: {RED}; }}
h2 {{ margin: 0; font-size: 40px; font-weight: 800; color: {ASH}; }}
section.jevto h2 {{ color: {PAPER}; }}
.n {{ font-size: 96px; font-weight: 900; letter-spacing: -0.045em; line-height: 1.02; }}
.n small {{ font-size: 32px; font-weight: 700; color: {ASH}; letter-spacing: 0; margin-left: 12px; }}
.v {{ font-size: 36px; font-weight: 800; margin: 6px 0 24px; }}
.ok {{ color: #7BD06A; }} .no {{ color: #FF5A52; }}
.t {{ font: 700 23px/1.6 Consolas, "Cascadia Mono", monospace; color: #BDB9B1; }}
.t div {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.t .gap {{ color: {ASH}; }}
.t .hit {{ background: {RED}; color: #fff; padding: 0 10px; margin: 0 -10px; }}
.t .more {{ color: #6A6E76; font-family: Chivo, sans-serif; font-size: 26px; margin-top: 12px; }}
</style><div class="w">
<h1>Same <code>git log</code>. Who keeps the answer?</h1>
<p>Goal: <b>“When did we change the reconnect backoff?”</b> · answer is commit 173 of 260</p>
<div class="g">{''.join(columns)}</div></div>"""
    shoot(shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          page, Path(__file__).resolve().parent / "compare-git-log.png", 1800, 820, 1, transparent=False)


if __name__ == "__main__":
    main()
