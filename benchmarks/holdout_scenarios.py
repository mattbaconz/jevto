"""Holdout and semantic scenarios for token_bench.py.

Written 2026-09-29 *after* reduction policy v9 was frozen, and run once. None
of these scenarios were used to tune JevTO's rules, so they measure how the
rules generalize rather than how well they fit the dev set.

* holdout   realistic outputs from tools and shapes the dev set does not cover
* semantic  vocabulary-mismatch cases where the goal and the answer share no
            words; deterministic rules are expected to miss or not cut, and
            these are the cases opt-in Jev ranking exists for
"""

from __future__ import annotations

# Every holdout run is logged here, including the ones that went badly.
RUN_LOG = """\
**Holdout run log.** Run 1 (policy v9, [`token-bench-run1-v9.json`](token-bench-run1-v9.json)) found two real JevTO bugs: \
`jevto run` could not start Windows `.cmd` shims, so the routed `tsc` command failed with exit 2 instead of 1 (`npm`, `npx`, \
`eslint` were equally affected); and goal words that match hundreds of log lines (`checkout`, `10:42`) overflowed the view \
budget, so the 434 KB JSON log fell back to full passthrough (0% cut). Both cargo scenarios did not run in any arm because the \
harness shell lacked the Rust toolchain (a harness error, not a result). Fixes: PATH/PATHEXT resolution for bare program names, \
and a stricter goal-word frequency cap (policy v9.1). Run 2 (below) is the first run with all nine scenarios valid; no rule \
was changed in response to a specific holdout output other than these two bug fixes. \
Live Jev run 1 (2026-09-30, [`token-bench-live1-v9.1.json`](token-bench-live1-v9.1.json)) exposed two adaptive-policy bugs: the \
adaptive search view re-protected matched `Err(`/`assert!` code lines (search-many-hits grew from 356 to 1,778 tokens), and a \
high need score (3.4 of 4) still kept only the single top-ranked file, dropping the rename in git-diff-rename-plus-fix. Fixed in \
policy v9.2 (search matches are not runtime facts; need levels set a minimum keep count); the stale cached decision was deleted \
and re-requested. The tables below are live run 2."""

import json
from pathlib import Path


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Holdout


def setup_cargo_multi_failure(root: Path) -> None:
    write(root / "Cargo.toml", '[package]\nname = "netcfg"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n')
    lib = [
        "pub fn parse_port(s: &str) -> Option<u16> { s.parse().ok() }",
        "pub fn nth(v: &[i32], i: usize) -> i32 { v[i] }",
        "pub fn region(map: &std::collections::HashMap<&str, &str>) -> String { map.get(\"region\").copied().unwrap().to_string() }",
        "",
    ]
    for module in ("parse", "lookup", "index", "misc"):
        lib.append(f"#[cfg(test)]\nmod {module}_tests {{\n    use super::*;")
        for n in range(100):
            lib.append(f"    #[test] fn {module}_case_{n:03}() {{ assert_eq!(parse_port(\"{1000 + n}\"), Some({1000 + n})); }}")
        if module == "parse":
            lib.append('    #[test] fn port_rejects_zero() { assert_eq!(parse_port("0"), None, "port 0 is reserved"); }')
        if module == "lookup":
            lib.append("    #[test] fn lookup_missing_region() { assert_eq!(region(&std::collections::HashMap::new()), \"eu\"); }")
        if module == "index":
            lib.append("    #[test] fn nth_out_of_range() { assert_eq!(nth(&[1, 2, 3], 5), 0); }")
        lib.append("}")
    write(root / "src" / "lib.rs", "\n".join(lib) + "\n")


def setup_tsc_errors(root: Path) -> None:
    write(root / "tsconfig.json", json.dumps({"compilerOptions": {"strict": True, "noEmit": True, "target": "es2022", "module": "es2022"}, "include": ["src"]}))
    for n in range(24):
        write(root / "src" / f"module{n:02}.ts", f"export function value{n}(x: number): number {{\n  return x * {n + 1};\n}}\n")
    write(root / "src" / "config.ts", "export const retries: number = \"3\";\n")
    write(root / "src" / "client.ts", "import { value3 } from './module03';\nexport const scaled = value3('7');\n")
    write(root / "src" / "user.ts", "interface User { id: number; name: string }\nexport function label(u: User): string {\n  return u.nickname;\n}\n")


def setup_go_pkg_failure(root: Path) -> None:
    write(root / "go.mod", "module example.com/app\n\ngo 1.22\n")
    for n in range(11):
        lines = [f"package pkg{n:02}", "", 'import "testing"', ""]
        for t in range(30):
            lines.append(f"func TestCase{t:02}(t *testing.T) {{ if {t}*2 != {t}+{t} {{ t.Fatal(\"math\") }} }}")
        write(root / f"pkg{n:02}" / "p_test.go", "\n".join(lines) + "\n")
    write(
        root / "billing" / "invoice_test.go",
        'package billing\n\nimport "testing"\n\n'
        "func TestInvoiceTotalRoundsHalfUp(t *testing.T) {\n"
        '\tgot := 10.04\n\tif got != 10.05 {\n\t\tt.Errorf("total = %.2f, want 10.05", got)\n\t}\n}\n',
    )


def setup_node_all_pass(root: Path) -> None:
    lines = ["const test = require('node:test');", "const assert = require('node:assert');", ""]
    for n in range(300):
        lines.append(f"test('formats amount {n:03}', () => {{ assert.strictEqual(({n} / 100).toFixed(2), '{n / 100:.2f}'); }});")
    write(root / "amount.test.js", "\n".join(lines) + "\n")


def setup_python_stdlib_traceback(root: Path) -> None:
    write(root / "config_loader.py", "import json\n\n\ndef load(text):\n    return json.loads(text)\n")
    lines = ["import unittest", "", "from config_loader import load", "", "", "class ConfigTest(unittest.TestCase):"]
    for n in range(60):
        lines.append(f"    def test_key_{n:02}(self):\n        self.assertEqual(load('{{\"k{n}\": {n}}}')['k{n}'], {n})\n")
    lines.append("    def test_load_rejects_trailing_comma(self):\n        self.assertEqual(load('{\"retries\": 3,}'), {'retries': 3})\n")
    lines.append("\nif __name__ == '__main__':\n    unittest.main()\n")
    write(root / "test_config.py", "\n".join(lines))


def setup_rg_todos(root: Path) -> None:
    areas = ["api", "cache", "cli", "db", "jobs", "mail", "payments", "search"]
    notes = ["tidy error message", "remove after migration", "add metrics", "handle unicode", "document this", "split function", "check null case", "log at debug"]
    for index in range(40):
        area = areas[index % len(areas)]
        body = []
        for line in range(4):
            body.append(f"// TODO: {notes[(index + line) % len(notes)]}")
            body.append(f"fn step_{line}() {{}}")
        if area == "payments" and index == 30:
            body.insert(3, "// TODO(payments): add rate limiting before retrying card charges")
        write(root / "src" / area / f"file_{index:02}.rs", "\n".join(body) + "\n")


def setup_git_rename_plus_fix(root: Path) -> None:
    from token_bench import git, git_env, init_repo

    init_repo(root)
    for n in range(20):
        write(
            root / "src" / "handlers" / f"h{n:02}.rs",
            f"use crate::util::fetch_config;\n\npub fn handle_{n:02}() {{\n    let cfg = fetch_config();\n    let retry = fetch_config().retries;\n    println!(\"{{}} {{}}\", cfg.name, retry);\n}}\n",
        )
    session = "pub fn expired(age: u64, max_age: u64) -> bool {\n    if age > max_age {\n        return true;\n    }\n    false\n}\n"
    write(root / "src" / "auth" / "session.rs", session)
    git(root, "add", "-A", env=git_env(1))
    git(root, "commit", "-qm", "base", env=git_env(1))
    for n in range(20):
        path = root / "src" / "handlers" / f"h{n:02}.rs"
        path.write_text(path.read_text(encoding="utf-8").replace("fetch_config", "load_settings"), encoding="utf-8", newline="\n")
    write(root / "src" / "auth" / "session.rs", session.replace("if age > max_age", "if age >= max_age"))


def setup_cargo_build_warnings(root: Path) -> None:
    write(root / "Cargo.toml", '[package]\nname = "noisy"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n')
    lines = []
    for n in range(40):
        lines.append(f"pub fn step_{n:02}() -> u32 {{\n    let unused_{n:02} = {n};\n    {n}\n}}\n")
    lines.append("pub fn total() -> u32 {\n    let count: u32 = \"12\";\n    count\n}\n")
    write(root / "src" / "lib.rs", "\n".join(lines))


def setup_json_log(root: Path) -> None:
    script = '''import json, random
random.seed(11)
services = ["cart", "catalog", "checkout", "search", "profile"]
for n in range(3000):
    ts = f"2026-09-29T10:{(n // 60) % 60:02d}:{n % 60:02d}.{n % 1000:03d}Z"
    if n == 2520:
        print(json.dumps({"ts": ts, "level": "error", "svc": "checkout", "msg": "payment authorization failed: card_declined (issuer code 51) for order 88121"}))
        continue
    svc = random.choice(services)
    print(json.dumps({"ts": ts, "level": "info", "svc": svc, "msg": "request served", "route": f"/api/{svc}", "status": random.choice([200, 200, 201, 304]), "ms": random.randint(2, 80)}))
'''
    write(root / "emit_log.py", script)


# ---------------------------------------------------------------------------
# Semantic (vocabulary mismatch)


def setup_semantic_log(root: Path) -> None:
    script = '''import random
random.seed(5)
subsystems = ["http", "db", "cache", "queue", "scheduler", "metrics", "grpc", "storage"]
verbs = ["handled", "flushed", "rotated", "synced", "polled", "compacted", "leased", "renewed"]
for n in range(60):
    print(f"boot: loaded module {n:02} ({random.choice(subsystems)}) config hash {random.getrandbits(32):08x} in {random.randint(1, 40)}ms")
for n in range(2400):
    ts = f"2026-09-29T11:{(n // 60) % 60:02d}:{n % 60:02d}Z"
    if n == 1700:
        print(f"{ts} WARN auth: refresh rejected for 1204 sessions, clock skew 340s exceeds 300s tolerance")
        continue
    s = random.choice(subsystems)
    print(f"{ts} INFO {s}: {random.choice(verbs)} {random.randint(1, 900)} items shard={random.randint(0, 63)} lag={random.randint(0, 40)}ms")
'''
    write(root / "print_log.py", script)


def setup_semantic_search(root: Path) -> None:
    for index in range(24):
        area = ["api", "db", "jobs", "net"][index % 4]
        body = [
            f"    let t = Duration::from_millis(cfg.timeout_ms + {index});",
            f"    conn.set_timeout(Some(t));",
            f"    tracing::trace!(timeout = ?t, \"{area} call\");",
        ]
        write(root / "src" / area / f"m{index:02}.rs", "fn call() {\n" + "\n".join(body) + "\n}\n")
    write(
        root / "src" / "vendors" / "charge_gateway.rs",
        "fn client() -> Client {\n    Client::builder().timeout(Duration::from_secs(12)).build()\n}\n",
    )


def setup_semantic_diff(root: Path) -> None:
    from token_bench import git, git_env, init_repo

    init_repo(root)
    files = {}
    for n in range(12):
        files[f"src/area{n:02}.rs"] = "".join(f"pub fn f{n}_{k}(x: u32) -> u32 {{ x + {k} }}\n" for k in range(20))
    files["src/money.rs"] = "pub fn to_units(cents: i64) -> f64 {\n    (cents as f64 / 100.0).round()\n}\n"
    for path, text in files.items():
        write(root / path, text)
    git(root, "add", "-A", env=git_env(1))
    git(root, "commit", "-qm", "base", env=git_env(1))
    for n in range(12):
        path = root / f"src/area{n:02}.rs"
        text = path.read_text(encoding="utf-8")
        text = text.replace(f"x + {n % 20}", f"x.saturating_add({n % 20})").replace(f"x + {(n + 7) % 20}", f"x.wrapping_add({(n + 7) % 20})")
        path.write_text(text + f"pub fn extra_{n}() -> u32 {{ {n} }}\n", encoding="utf-8", newline="\n")
    write(root / "src/money.rs", files["src/money.rs"].replace(".round()", ".floor()"))


HOLDOUT = [
    {"name": "cargo-multi-failure", "suite": "holdout", "setup": setup_cargo_multi_failure,
     "warmup": ["cargo", "test", "--no-run", "--quiet"], "command": "cargo test", "goal": "Fix the failing tests",
     "needles": ["port_rejects_zero", "port 0 is reserved", "lookup_missing_region", "called `Option::unwrap()` on a `None` value",
                 "nth_out_of_range", "the len is 3 but the index is 5"], "requires": "cargo"},
    {"name": "tsc-type-errors", "suite": "holdout", "setup": setup_tsc_errors, "command": "tsc -p .",
     "goal": "Fix the type errors", "needles": ["TS2322", "TS2345", "TS2339"], "requires": "tsc"},
    {"name": "go-package-failure", "suite": "holdout", "setup": setup_go_pkg_failure,
     "warmup": ["go", "test", "-count=1", "-run", "^$", "./..."], "command": "go test -count=1 ./...",
     "goal": "Fix the failing billing test", "needles": ["TestInvoiceTotalRoundsHalfUp", "want 10.05"], "requires": "go"},
    {"name": "node-all-pass", "suite": "holdout", "setup": setup_node_all_pass, "command": "node --test",
     "goal": "Make sure the amount formatting tests pass", "needles": ["pass 300", "fail 0"], "requires": "node"},
    {"name": "python-stdlib-traceback", "suite": "holdout", "setup": setup_python_stdlib_traceback, "command": "python -m unittest -v",
     "goal": "Fix the config loading error", "needles": ["test_load_rejects_trailing_comma", "config_loader.py", "JSONDecodeError"]},
    {"name": "rg-todo-sweep", "suite": "holdout", "setup": setup_rg_todos, "command": "rg -n -H TODO src --sort=path",
     "goal": "Find the TODO about rate limiting in the payments module", "needles": ["TODO(payments): add rate limiting"], "requires": "rg"},
    {"name": "git-diff-rename-plus-fix", "suite": "holdout", "setup": setup_git_rename_plus_fix, "command": "git diff",
     "goal": "Review this change before merge", "needles": ["if age >= max_age", "load_settings"]},
    {"name": "cargo-build-warnings", "suite": "holdout", "setup": setup_cargo_build_warnings, "command": "cargo build",
     "goal": "Fix the build", "needles": ["E0308", "mismatched types", "unused_00"], "requires": "cargo"},
    {"name": "json-log-triage", "suite": "holdout", "setup": setup_json_log, "command": "python emit_log.py", "wrap": True,
     "goal": "Why did checkout fail around 10:42?", "needles": ["card_declined"]},
]

SEMANTIC = [
    {"name": "semantic-log-signout", "suite": "semantic", "setup": setup_semantic_log, "command": "python print_log.py", "wrap": True,
     "goal": "Why are people being signed out of the app?", "needles": ["clock skew 340s exceeds 300s tolerance"]},
    {"name": "semantic-search-deadline", "suite": "semantic", "setup": setup_semantic_search, "command": "rg -n -H timeout src --sort=path",
     "goal": "How long do we wait on the card processor before giving up?", "needles": ["Duration::from_secs(12)"], "requires": "rg"},
    {"name": "semantic-diff-rounding", "suite": "semantic", "setup": setup_semantic_diff, "command": "git diff",
     "goal": "Did anything change how refunds get rounded?", "needles": [".floor()"]},
]
