"""Reproducible payload benchmark: native vs RTK vs JevTO on real commands.

Each scenario builds a small, realistic workspace, then runs the same command
through four arms, each chosen by the tool's own Claude Code hook:

* native            the command as the agent typed it
* rtk               whatever `rtk hook claude` rewrites it to (native if RTK
                    does not rewrite it)
* jevto             whatever `jevto hook claude-pre` rewrites it to, with the
                    scenario goal recorded through `jevto hook claude-prompt`
* jevto-adaptive    the same, with opt-in Jev (Choice + Noul + Score) using a
                    committed decision cache, or a live call when
                    OPENROUTER_API_KEY is set and --live-jev is passed

Metrics per arm: delivered bytes, estimated tokens (bytes / 4, the convention
RTK also uses), reduction versus native, answerability (fraction of scenario
"needles" -- the exact facts an agent needs -- visible without recall), exit
parity with native, and Jev calls/cost. This measures what reaches the agent's
context for one command. It is not a whole-task, provider-billed result; see
the agent-level pilots for that.

Usage:
    python benchmarks/token_bench.py [--jevto PATH] [--scenarios a,b] [--live-jev]
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "benchmarks" / "jev-cache"
RESULTS_DIR = ROOT / "benchmarks" / "results"
WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# Scenario workspaces


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def git(root: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def git_env(stamp: int) -> dict:
    env = os.environ.copy()
    date = f"{1767225600 + stamp * 3600} +0000"
    env.update(
        GIT_AUTHOR_NAME="Bench",
        GIT_AUTHOR_EMAIL="bench@example.invalid",
        GIT_COMMITTER_NAME="Bench",
        GIT_COMMITTER_EMAIL="bench@example.invalid",
        GIT_AUTHOR_DATE=date,
        GIT_COMMITTER_DATE=date,
    )
    return env


def init_repo(root: Path) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "commit.gpgsign", "false")


WORDS = ["alpha", "bravo", "cobalt", "delta", "ember", "falcon", "garnet", "harbor", "indigo", "juniper"]


def setup_rust_failure(root: Path) -> None:
    write(root / "Cargo.toml", '[package]\nname = "headers"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n')
    tests = []
    for n in range(150):
        tests.append(f"    #[test]\n    fn case_{n:03}() {{ assert_eq!(parse_header(\"k{n}=v\"), Some((\"k{n}\", \"v\"))); }}\n")
    tests.append(
        "    #[test]\n    fn parse_header_rejects_leading_space() {\n"
        "        assert_eq!(parse_header(\" k=v\"), None, \"leading space must be rejected\");\n    }\n"
    )
    write(
        root / "src" / "lib.rs",
        "pub fn parse_header(line: &str) -> Option<(&str, &str)> {\n"
        "    line.split_once('=')\n}\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n"
        + "".join(tests)
        + "}\n",
    )


def setup_rust_lean(root: Path) -> None:
    write(root / "Cargo.toml", '[package]\nname = "tiny"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n')
    write(
        root / "src" / "lib.rs",
        "pub fn add(a: i32, b: i32) -> i32 { a + b }\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n"
        "    #[test] fn adds() { assert_eq!(add(1, 2), 3); }\n"
        "    #[test] fn negative() { assert_eq!(add(-1, -2), -3); }\n"
        "    #[test] fn zero() { assert_eq!(add(0, 0), 0); }\n}\n",
    )


def setup_go_failure(root: Path) -> None:
    write(root / "go.mod", "module example.com/headers\n\ngo 1.22\n")
    lines = ["package headers", "", 'import "testing"', ""]
    for n in range(200):
        lines.append(f"func TestCase{n:03}(t *testing.T) {{ if {n}+{n} != 2*{n} {{ t.Fatal(\"arith\") }} }}")
    lines.append(
        "func TestParseHeaderRejectsLeadingSpace(t *testing.T) { got := 3; "
        'if got != 4 { t.Fatalf("parse_header: expected 4 fields, got %d", got) } }'
    )
    write(root / "headers_test.go", "\n".join(lines) + "\n")


def setup_python_failure(root: Path) -> None:
    lines = ["import unittest", "", "", "def parse(line):", "    key, _, value = line.partition('=')", "    return value or None", "", "", "class ParserTest(unittest.TestCase):"]
    for n in range(220):
        lines.append(f"    def test_case_{n:03}(self):\n        self.assertEqual(parse('k{n}=v'), 'v')\n")
    lines.append("    def test_rejects_leading_space(self):\n        self.assertIsNone(parse(' value=7'), 'leading-space lines are not headers')\n")
    lines.append("\nif __name__ == '__main__':\n    unittest.main()\n")
    write(root / "test_parser.py", "\n".join(lines))


def setup_python_quiet_warning(root: Path) -> None:
    lines = ["import sys", "import unittest", "", "", "class CacheTest(unittest.TestCase):"]
    for n in range(220):
        body = "        self.assertTrue(True)\n"
        if n == 117:
            body = "        print('warning: cache invalidated before flush; stale reads possible', file=sys.stderr)\n" + body
        lines.append(f"    def test_cache_{n:03}(self):\n{body}")
    lines.append("\nif __name__ == '__main__':\n    unittest.main()\n")
    write(root / "test_cache.py", "\n".join(lines))


def setup_node_failure(root: Path) -> None:
    lines = ["const test = require('node:test');", "const assert = require('node:assert');", "", "const slug = (s) => s.toLowerCase().replace(/\\s+/g, '-');", ""]
    for n in range(180):
        lines.append(f"test('slug case {n:03}', () => {{ assert.strictEqual(slug('A B{n}'), 'a-b{n}'); }});")
    lines.append("test('slug trims surrounding whitespace', () => { assert.strictEqual(slug('  Hello World  '), 'hello-world'); });")
    write(root / "slug.test.js", "\n".join(lines) + "\n")


def setup_service_log(root: Path) -> None:
    script = '''import random
random.seed(7)
words = ["alpha", "bravo", "cobalt", "delta", "ember", "falcon", "garnet", "harbor", "indigo", "juniper"]
routes = ["/api/cart", "/api/items", "/api/search", "/api/profile", "/api/orders", "/health"]
for n in range(2400):
    ts = f"2026-09-29T10:{(n // 60) % 60:02d}:{n % 60:02d}Z"
    if n == 1311:
        print(f"{ts} INFO session-store: evicted 4812 entries under memory pressure (limit 512MiB)")
        continue
    if n == 1890:
        print(f"{ts} ERROR payments: upstream timeout after 3000ms (attempt 3/3)")
        continue
    w = [random.choice(words) for _ in range(3)]
    route = random.choice(routes)
    print(f"{ts} INFO http: {route} {random.choice([200, 200, 200, 204, 304])} {random.randint(3, 90)}ms req={w[0]}-{w[1]}-{random.randint(1000, 9999)} node={w[2]}")
'''
    write(root / "print_log.py", script)


def setup_rg_many_hits(root: Path) -> None:
    # Call sites in `api/` and `worker/` sort around the one definition in
    # `net/`, so the answer sits in the middle of the search output.
    for index in range(26):
        area = "api" if index < 13 else "worker"
        name = f"{WORDS[index % 10]}_{index:02}"
        word = WORDS[(index * 7) % 10]
        body = [
            f"    let remaining_{word} = settings.retry_budget.saturating_sub(attempts_{index});",
            f"    if attempts_{index} >= cfg.retry_budget {{ return Err(Error::{word.title()}Exhausted); }}",
            f"    tracing::debug!(retry_budget = settings.retry_budget, stage = \"{word}\");",
            f"    self.retry_budget = other.retry_budget.min({index % 9 + 2});",
            f"    assert!(opts.retry_budget > 0, \"{name} requires retries\");",
            f"    metrics.gauge(\"{word}.retry_budget\", state.retry_budget as f64);",
        ]
        write(root / "src" / area / f"{name}.rs", f"pub fn run_{name}(settings: &Settings) {{\n" + "\n".join(body) + "\n}}\n")
    write(
        root / "src" / "net" / "limits.rs",
        "/// Upper bound on reconnect attempts before surfacing an error.\n"
        "pub fn default_retry_budget() -> u32 {\n    5\n}\npub const RETRY_BUDGET_FIELD: &str = \"retry_budget\";\n",
    )


def setup_git_diff_lockfile(root: Path) -> None:
    init_repo(root)
    lock = "".join(
        f'[[package]]\nname = "dep-{n:04}"\nversion = "1.{n % 17}.0"\nchecksum = "{n:064x}"\n\n' for n in range(300)
    )
    write(root / "Cargo.lock", lock)
    write(root / "src" / "http.rs", "pub fn parse_header(line: &str) -> Option<(&str, &str)> {\n    line.split_once('=')\n}\n")
    git(root, "add", "-A", env=git_env(1))
    git(root, "commit", "-qm", "base", env=git_env(1))
    write(root / "Cargo.lock", lock.replace('version = "1.', 'version = "2.'))
    write(
        root / "src" / "http.rs",
        "pub fn parse_header(line: &str) -> Option<(&str, &str)> {\n"
        "    if line.starts_with(' ') {\n        return None;\n    }\n    line.split_once('=')\n}\n",
    )


def setup_git_diff_multi_file(root: Path) -> None:
    init_repo(root)
    modules = [f"{WORDS[n % 10]}_{n:02}" for n in range(13)]
    for name in modules:
        lines = [f"pub fn handle_{name}(ctx: &Context) -> Result<()> {{"]
        for step in range(14):
            lines.append(f"    log_info(ctx, \"{name} step {step}\");")
            lines.append(f"    ctx.metrics.incr(\"{name}.step_{step}\");")
        lines.append("    Ok(())\n}")
        write(root / "src" / "handlers" / f"{name}.rs", "\n".join(lines) + "\n")
    bucket = (
        "pub struct Bucket { tokens: f64, capacity: f64, rate: f64 }\n\n"
        "impl Bucket {\n    pub fn refill(&mut self, elapsed: f64) {\n"
        "        self.tokens = self.tokens + elapsed * self.rate;\n    }\n}\n"
    )
    write(root / "src" / "limiter" / "bucket.rs", bucket)
    git(root, "add", "-A", env=git_env(1))
    git(root, "commit", "-qm", "base", env=git_env(1))
    for name in modules:
        path = root / "src" / "handlers" / f"{name}.rs"
        path.write_text(path.read_text(encoding="utf-8").replace("log_info(ctx, ", "tracing::info!(target: \"app\", "), encoding="utf-8", newline="\n")
    write(root / "src" / "limiter" / "bucket.rs", bucket.replace(
        "        self.tokens = self.tokens + elapsed * self.rate;\n",
        "        self.tokens = (self.tokens + elapsed * self.rate).min(self.capacity);\n",
    ))


def setup_git_log(root: Path) -> None:
    init_repo(root)
    subjects = []
    for n in range(260):
        subjects.append(f"Refine {WORDS[n % 10]} module step {n}")
    subjects[173] = "Increase reconnect backoff to 250ms after flaky CI runs"
    for n, subject in enumerate(subjects):
        write(root / "CHANGES.txt", f"{n}\n")
        git(root, "add", "-A", env=git_env(n))
        git(root, "commit", "-qm", subject, "-m", f"Body line for change {n}.", env=git_env(n))


SCENARIOS = [
    {
        "name": "rust-test-failure",
        "setup": setup_rust_failure,
        "warmup": ["cargo", "test", "--no-run", "--quiet"],
        "command": "cargo test",
        "goal": "Fix the failing parse_header test",
        "needles": ["parse_header_rejects_leading_space", "leading space must be rejected", "150 passed"],
        "requires": "cargo",
    },
    {
        "name": "go-test-failure",
        "setup": setup_go_failure,
        "warmup": ["go", "test", "-count=1", "-run", "^$", "./..."],
        "command": "go test -v -count=1 ./...",
        "goal": "Fix the failing header parser test",
        "needles": ["TestParseHeaderRejectsLeadingSpace", "expected 4 fields, got 3"],
        "requires": "go",
    },
    {
        "name": "python-unittest-failure",
        "setup": setup_python_failure,
        "command": "python -m unittest -v",
        "goal": "Fix the failing parser test",
        "needles": ["test_rejects_leading_space", "leading-space lines are not headers", "failures=1"],
    },
    {
        "name": "python-quiet-warning",
        "setup": setup_python_quiet_warning,
        "command": "python -m unittest -v",
        "goal": "Confirm the cache suite passes",
        "needles": ["cache invalidated before flush", "220 tests"],
    },
    {
        "name": "node-test-failure",
        "setup": setup_node_failure,
        "command": "node --test",
        "goal": "Fix the failing slug test",
        "needles": ["slug trims surrounding whitespace", "hello-world"],
        "requires": "node",
    },
    {
        "name": "rust-test-lean",
        "setup": setup_rust_lean,
        "warmup": ["cargo", "test", "--no-run", "--quiet"],
        "command": "cargo test",
        "goal": "Run the tests",
        "needles": ["3 passed"],
        "requires": "cargo",
    },
    {
        "name": "service-log-triage",
        "setup": setup_service_log,
        "command": "python print_log.py",
        "wrap": True,
        "goal": "Why are users suddenly getting logged out of the app?",
        "needles": ["evicted 4812 entries", "upstream timeout after 3000ms"],
        "semantic_needles": ["evicted 4812 entries"],
    },
    {
        "name": "search-many-hits",
        "setup": setup_rg_many_hits,
        "command": "rg -n -H retry_budget src --sort=path",
        "goal": "How many times do we retry before giving up?",
        "needles": ["fn default_retry_budget() -> u32"],
        "semantic_needles": ["fn default_retry_budget() -> u32"],
        "requires": "rg",
    },
    {
        "name": "git-diff-lockfile",
        "setup": setup_git_diff_lockfile,
        "command": "git diff",
        "goal": "Review the header parsing change",
        "needles": ["starts_with(' ')", "Cargo.lock"],
    },
    {
        "name": "git-diff-multi-file",
        "setup": setup_git_diff_multi_file,
        "command": "git diff",
        "goal": "Check that tokens can no longer exceed the maximum when the limiter refills",
        "needles": [".min(self.capacity)"],
        "semantic_needles": [".min(self.capacity)"],
    },
    {
        "name": "git-log-history",
        "setup": setup_git_log,
        "command": "git log",
        "goal": "When did we change the reconnect backoff?",
        "needles": ["Increase reconnect backoff to 250ms"],
    },
]
# The scenarios above are the dev set: JevTO's rules were tuned while running
# them. Holdout and semantic scenarios were written after the rules froze.
for _scenario in SCENARIOS:
    _scenario.setdefault("suite", "dev")
from holdout_scenarios import HOLDOUT, RUN_LOG, SEMANTIC  # noqa: E402

SCENARIOS += HOLDOUT + SEMANTIC
SUITES = {
    "dev": "Dev set (rules were tuned on these)",
    "holdout": "Holdout set (written after the rules froze; run once)",
    "semantic": "Semantic set (goal and answer share no words; the Jev cases)",
}
JEV_USD_PER_INPUT_TOKEN = 0.042 / 1_000_000


# ---------------------------------------------------------------------------
# Arms


def find_bash() -> str:
    if WINDOWS:
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if Path(candidate).is_file():
                return candidate
    found = shutil.which("bash")
    if not found:
        raise SystemExit("bash is required to execute hook-rewritten commands")
    return found


def hook_rewrite(argv: list[str], command: str, session: str, env: dict) -> str | None:
    event = json.dumps({"session_id": session, "tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(argv, input=event.encode(), capture_output=True, env=env, timeout=30)
    text = result.stdout.decode("utf-8", "replace").strip()
    if not text:
        return None
    return json.loads(text)["hookSpecificOutput"]["updatedInput"]["command"]


def run_bash(bash: str, command: str, cwd: Path, env: dict) -> tuple[int, bytes, float]:
    started = time.perf_counter()
    result = subprocess.run([bash, "-c", command], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    return result.returncode, result.stdout, time.perf_counter() - started


def tokens(size: int) -> int:
    return math.ceil(size / 4)


def measure(view: bytes, needles: list[str]) -> dict:
    text = view.decode("utf-8", "replace")
    found = [needle for needle in needles if needle in text]
    return {"bytes": len(view), "est_tokens": tokens(len(view)), "needles_found": len(found), "needles_total": len(needles), "missing": [n for n in needles if n not in found]}


def seed_cache(store: Path) -> None:
    if CACHE_DIR.is_dir():
        target = store / "jev-cache"
        target.mkdir(parents=True, exist_ok=True)
        for entry in CACHE_DIR.glob("*.json"):
            shutil.copy2(entry, target / entry.name)


def harvest_cache(store: Path) -> int:
    source = store / "jev-cache"
    if not source.is_dir():
        return 0
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    added = 0
    for entry in source.glob("*.json"):
        destination = CACHE_DIR / entry.name
        if not destination.exists():
            shutil.copy2(entry, destination)
            added += 1
    return added


def receipt_jev(store: Path) -> dict:
    info = {"calls": 0, "cache_hits": 0, "cost_usd": 0.0, "input_tokens": 0, "fallback": None, "decision": None, "request": None}
    for path in (store / "receipts").glob("*.json"):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        usage = receipt.get("jev_usage")
        if usage:
            info["calls"] += 1
            info["cost_usd"] += usage.get("billed_cost") or 0.0
            info["input_tokens"] += usage.get("total_input_tokens") or 0
        if receipt.get("jev_cache_hit"):
            info["cache_hits"] += 1
        info["fallback"] = receipt.get("adaptive_fallback") or info["fallback"]
        info["decision"] = receipt.get("jev_decision") or info["decision"]
        info["request"] = receipt.get("jev_request") or info["request"]
    return info


def local_paths(args) -> list[tuple[str, str]]:
    """Machine-specific strings replaced in saved artifacts (longest first)."""
    jevto = Path(args.jevto)
    home = Path.home()
    temp = Path(tempfile.gettempdir())
    pairs = [
        (jevto.as_posix(), "<jevto>"),
        (str(jevto), "<jevto>"),
        (temp.as_posix(), "<tmp>"),
        (str(temp), "<tmp>"),
        (temp.resolve().as_posix(), "<tmp>"),
        (str(temp.resolve()), "<tmp>"),
        (home.as_posix(), "~"),
        (str(home), "~"),
    ]
    pairs += [(json.dumps(local)[1:-1], placeholder) for local, placeholder in pairs]
    return sorted({pair for pair in pairs if len(pair[0]) > 3}, key=lambda pair: -len(pair[0]))


def sanitize(text: str, args) -> str:
    for local, placeholder in local_paths(args):
        text = text.replace(local, placeholder)
    return text


def save_view(args, scenario: dict, arm: str, view: bytes) -> None:
    directory = Path(args.out) / "views" / scenario["name"]
    directory.mkdir(parents=True, exist_ok=True)
    text = sanitize(view.decode("utf-8", "replace"), args)
    (directory / f"{arm}.txt").write_text(text, encoding="utf-8", newline="")


def run_scenario(scenario: dict, args, bash: str) -> dict:
    record = {"name": scenario["name"], "suite": scenario["suite"], "command": scenario["command"], "goal": scenario["goal"], "arms": {}}
    requirement = scenario.get("requires")
    if requirement and not shutil.which(requirement):
        record["skipped"] = f"{requirement} not installed"
        return record
    with tempfile.TemporaryDirectory(prefix=f"jevto-bench-{scenario['name']}-") as temporary:
        root = Path(temporary) / "work"
        root.mkdir()
        scenario["setup"](root)
        env = os.environ.copy()
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1", "CARGO_TERM_COLOR": "never", "GIT_PAGER": "cat", "PAGER": "cat"})
        if scenario.get("warmup"):
            subprocess.run(scenario["warmup"], cwd=root, env=env, capture_output=True, timeout=900)
        session = f"bench-{scenario['name']}"

        native_exit, native, native_seconds = run_bash(bash, scenario["command"], root, env)
        record["native_exit"] = native_exit
        record["arms"]["native"] = {**measure(native, scenario["needles"]), "rewritten": None, "exit": native_exit, "seconds": round(native_seconds, 3)}
        save_view(args, scenario, "native", native)

        if shutil.which("rtk"):
            rewritten = hook_rewrite(["rtk", "hook", "claude"], scenario["command"], session, env)
            command = rewritten or scenario["command"]
            exit_code, view, seconds = run_bash(bash, command, root, env)
            record["arms"]["rtk"] = {**measure(view, scenario["needles"]), "rewritten": rewritten, "exit": exit_code, "seconds": round(seconds, 3)}
            save_view(args, scenario, "rtk", view)

        for arm, adaptive in (("jevto", False), ("jevto-adaptive", True)):
            store = Path(temporary) / f"store-{arm}"
            arm_env = dict(env, JEVTO_STORE_DIR=str(store))
            if not adaptive:
                # Rules only, even when a key is present (auto mode would use Jev).
                arm_env["JEVTO_MODE"] = "rules"
                arm_env.pop("OPENROUTER_API_KEY", None)
            elif args.live_jev and arm_env.get("OPENROUTER_API_KEY"):
                # The product default: auto mode, key present, implicit
                # workspace policy. No flags, no policy file.
                arm_env.pop("JEVTO_MODE", None)
                seed_cache(store)
            if adaptive and not (args.live_jev and arm_env.get("OPENROUTER_API_KEY")):
                policy = Path(temporary) / "policy.json"
                policy.write_text(json.dumps({
                    "schema_version": 1,
                    "data_classification": "synthetic",
                    "allow_search_snippets": True,
                    "allow_output_snippets": True,
                    "allow_diff_snippets": True,
                    "allowed_roots": [str(root)],
                }), encoding="utf-8")
                arm_env.update(JEVTO_MODE="adaptive", JEVTO_REMOTE_POLICY=str(policy), JEVTO_ALLOW_REMOTE_JEV="1")
                if not args.live_jev:
                    arm_env.pop("OPENROUTER_API_KEY", None)
                seed_cache(store)
            prompt = json.dumps({"session_id": session, "prompt": scenario["goal"], "hook_event_name": "UserPromptSubmit"})
            subprocess.run([args.jevto, "hook", "claude-prompt"], input=prompt.encode(), env=arm_env, check=True, timeout=30)
            command = scenario["command"]
            rewritten = hook_rewrite([args.jevto, "hook", "claude-pre"], command, session, arm_env)
            routing = "hook" if rewritten else "native"
            if rewritten is None and scenario.get("wrap"):
                # A log dump is not an allowlisted program; use the explicit wrapper.
                rewritten = f"'{Path(args.jevto).as_posix()}' run --host claude --session {session} --workspace-id claude -- {command}"
                routing = "explicit"
            exit_code, view, seconds = run_bash(bash, rewritten or command, root, arm_env)
            result = {**measure(view, scenario["needles"]), "rewritten": rewritten, "routing": routing, "exit": exit_code, "seconds": round(seconds, 3)}
            if adaptive:
                result["jev"] = receipt_jev(store)
                result["cache_added"] = harvest_cache(store)
            record["arms"][arm] = result
            save_view(args, scenario, arm, view)
    # A missed fact sends the agent back for the full output: charge the
    # native payload on top of the view (the same penalty for every tool).
    native_tokens = record["arms"]["native"]["est_tokens"]
    for data in record["arms"].values():
        missed = data["needles_found"] < data["needles_total"]
        data["effective_tokens"] = data["est_tokens"] + (native_tokens if missed else 0)
    return record


ARMS = ["native", "rtk", "jevto", "jevto-adaptive"]
ARM_TITLES = {"native": "Native", "rtk": "RTK", "jevto": "JevTO", "jevto-adaptive": "JevTO + Jev"}


def arm_cell(record: dict, arm: str) -> str:
    data = record["arms"].get(arm)
    if not data:
        return "n/a"
    native = record["arms"]["native"]["est_tokens"]
    facts = f"{data['needles_found']}/{data['needles_total']}"
    if arm == "native":
        return f"{data['est_tokens']:,} · {facts}"
    change = 100.0 * (native - data["est_tokens"]) / native if native else 0.0
    note = ""
    if not data.get("rewritten"):
        note = " (not routed)"
    if data.get("routing") == "explicit":
        note = " (explicit `jevto run`)"
    if data["exit"] != record["native_exit"]:
        note += " exit≠native"
    miss = " ✗" if data["needles_found"] < data["needles_total"] else ""
    return f"{data['est_tokens']:,} (−{change:.0f}%) · {facts}{miss}{note}"


def suite_totals(rows: list[dict]) -> dict:
    totals = {}
    for arm in ARMS:
        present = [row["arms"][arm] for row in rows if arm in row["arms"]]
        totals[arm] = {
            "tokens": sum(d["est_tokens"] for d in present),
            "effective": sum(d["effective_tokens"] for d in present),
            "found": sum(d["needles_found"] for d in present),
            "total": sum(d["needles_total"] for d in present),
        }
    return totals


def total_cell(totals: dict, arm: str) -> str:
    data, native = totals[arm], totals["native"]
    if not data["total"]:
        return "n/a"
    facts = f"{data['found']}/{data['total']}"
    if arm == "native":
        return f"**{data['tokens']:,}** · {facts}"
    change = 100.0 * (native["tokens"] - data["tokens"]) / native["tokens"] if native["tokens"] else 0.0
    return f"**{data['tokens']:,} (−{change:.0f}%)** · {facts}"


def render_markdown(results: list[dict], meta: dict) -> str:
    ran = [r for r in results if not r.get("skipped")]
    lines = [
        "# JevTO token benchmark",
        "",
        f"Generated {meta['generated']} with `{meta['jevto']}` ({meta['jevto_version']}), RTK {meta['rtk_version']}, on {meta['platform']}.",
        "",
        "**Method.** Every scenario builds a small real workspace and runs one real command. Each arm is chosen by that tool's own Claude Code hook "
        "(`rtk hook claude`, `jevto hook claude-pre`), so an arm is whatever the tool actually does when an agent types the command. "
        "Tokens are bytes/4 estimates of what reaches the agent for that one command. **Facts** are exact strings the task needs (needles), "
        "counted when visible without a recall. **Effective tokens** add the native payload whenever a fact is missing, because the agent then has "
        "to go back for the full output; the same penalty applies to every tool.",
        "",
        "**Suites.** " + " ".join(f"*{name}*: {title}." for name, title in SUITES.items()),
        "",
        "## Summary",
        "",
        "| Suite | Scenarios | " + " | ".join(ARM_TITLES[a] for a in ARMS) + " | Effective tokens (Native → RTK → JevTO) |",
        "| --- | ---: | " + " | ".join("---:" for _ in ARMS) + " | --- |",
    ]
    for suite in list(SUITES) + ["all"]:
        rows = ran if suite == "all" else [r for r in ran if r.get("suite") == suite]
        if not rows:
            continue
        totals = suite_totals(rows)
        effective = " → ".join(f"{totals[a]['effective']:,}" for a in ("native", "rtk", "jevto"))
        name = "**All**" if suite == "all" else suite
        lines.append(f"| {name} | {len(rows)} | " + " | ".join(total_cell(totals, a) for a in ARMS) + f" | {effective} |")
    for suite, title in SUITES.items():
        rows = [r for r in results if r.get("suite") == suite]
        if not rows:
            continue
        lines += ["", f"## {title}", ""]
        if suite == "holdout":
            lines += [RUN_LOG, ""]
        lines += ["| Scenario | Command | " + " | ".join(ARM_TITLES[a] for a in ARMS) + " |",
                  "| --- | --- | " + " | ".join("---:" for _ in ARMS) + " |"]
        for record in rows:
            if record.get("skipped"):
                lines.append(f"| {record['name']} | `{record['command']}` | skipped: {record['skipped']} |" + " |" * (len(ARMS) - 1))
                continue
            lines.append(f"| {record['name']} | `{record['command']}` | " + " | ".join(arm_cell(record, a) for a in ARMS) + " |")
    lines += [
        "",
        "## Jev requests (adaptive arm)",
        "",
        "What JevTO would send to Jev for each scenario, measured before any network call. Long-output candidates are sent as line-shape digests "
        "(rare lines verbatim, repeats counted), so a 200 KB log becomes a small request. Cost uses $0.042 per million input tokens (bytes/4).",
        "",
        "| Scenario | Kind | Candidates | Request | Est. cost per decision | Calls | Outcome |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for record in ran:
        data = record["arms"].get("jevto-adaptive")
        if not data:
            continue
        jev = data.get("jev", {})
        request = jev.get("request") or {}
        size = request.get("bytes")
        cost = f"${size / 4 * JEV_USD_PER_INPUT_TOKEN:.5f}" if size else ""
        decision = jev.get("decision") or {}
        outcome = f"kept {decision.get('kept')}/{decision.get('candidates')}, need {decision.get('need', 0):.1f}" if decision else (jev.get("fallback") or "")
        lines.append(
            f"| {record['name']} | {request.get('kind', '')} | {request.get('candidates', '')} | "
            f"{f'{size / 1024:.1f} KB' if size else ''} | {cost} | {jev.get('calls', 0)} | {outcome} |"
        )
    lines += [
        "",
        "`missing_jev_key` means the request was prepared and sized but not sent. `no_rankable_sections` / `no_reducible_sections` mean the "
        "deterministic view already had nothing left to rank, so Jev is never called.",
        "",
        "Reproduce: `cargo build --release -p jevto && python benchmarks/token_bench.py`. Committed Jev decisions in `benchmarks/jev-cache/` replay "
        "without a key; `--live-jev` with `OPENROUTER_API_KEY` set makes fresh calls for cache misses only.",
        "",
    ]
    return "\n".join(lines)


def version(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "not installed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default = ROOT / "target" / "release" / ("jevto.exe" if WINDOWS else "jevto")
    parser.add_argument("--jevto", default=str(default))
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--suite", default="", help="comma list of dev,holdout,semantic (default: all)")
    parser.add_argument("--live-jev", action="store_true", help="allow live Jev calls for cache misses (needs OPENROUTER_API_KEY)")
    parser.add_argument("--out", default=str(RESULTS_DIR))
    parser.add_argument("--extra-path", action="append", default=[], help="directory prepended to PATH (e.g. a bundled rg or node)")
    parser.add_argument("--render-only", action="store_true", help="rewrite token-bench.md from the saved JSON without running")
    args = parser.parse_args()
    if args.render_only:
        saved = json.loads((Path(args.out) / "token-bench.json").read_text(encoding="utf-8"))
        (Path(args.out) / "token-bench.md").write_text(render_markdown(saved["results"], saved["meta"]), encoding="utf-8")
        return 0
    for directory in reversed(args.extra_path):
        os.environ["PATH"] = directory + os.pathsep + os.environ["PATH"]
    if not Path(args.jevto).is_file():
        raise SystemExit(f"build JevTO first: {args.jevto}")
    args.jevto = str(Path(args.jevto).resolve())
    bash = find_bash()
    selected = [s for s in SCENARIOS if (not args.scenarios or s["name"] in args.scenarios.split(","))
                and (not args.suite or s["suite"] in args.suite.split(","))]
    results = []
    for scenario in selected:
        print(f"[bench] {scenario['name']} ...", flush=True)
        record = run_scenario(scenario, args, bash)
        results.append(record)
        for arm, data in record.get("arms", {}).items():
            print(f"    {arm:<15} {data['est_tokens']:>7,} tok  {data['needles_found']}/{data['needles_total']} needles  exit={data['exit']}", flush=True)
    meta = {
        "generated": time.strftime("%Y-%m-%d"),
        "jevto": Path(args.jevto).name,
        "jevto_version": version([args.jevto, "--version"]),
        "rtk_version": version(["rtk", "--version"]),
        "platform": sys.platform,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "token-bench.json").write_text(sanitize(json.dumps({"meta": meta, "results": results}, indent=2), args), encoding="utf-8")
    (out / "token-bench.md").write_text(render_markdown(results, meta), encoding="utf-8")
    print(f"[bench] wrote {out / 'token-bench.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
