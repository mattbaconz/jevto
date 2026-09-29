# Claude Code coding pilot — 2026-09-28

This focused local pilot tested Claude Code `2.1.283` on the pinned `claude-haiku-4-5-20251001` snapshot. It measured **explicitly instructed** `jevto run` verifier calls, not automatic Claude shell interception. The five measured arms ran in this frozen order: Rust native, Rust adaptive, Rust deterministic, Python deterministic, Python native. Claude's terminal `modelUsage` named only the pinned model in every arm. The signed-in route was a Pro subscription; Claude's dollar values below are client-side list-price estimates, not a bill.

The [harness](claude_coding_pilot.py) records the prompts, initial trees, frozen holdout hashes, CLI and JevTO binary hashes, terminal streams, receipts, exact recalls, final diffs, and independent checks. Local artifacts: [manifest](../.bench-runs/claude-coding-pilot-20260928T081929Z/manifest.json) and the five sibling arm directories. Claude CLI SHA256 was `9dbe16dafed59da5cdabbfe11ad0335738c753fad794989b47f9446accd6de3a`; JevTO's executable SHA256 was `469b14ed1dc601ce37a623edf3801ff9bf682a8207ab7f1ff998db65b538495a`.

## Task and run controls

- Each Rust arm began at Git tree `f2a40e2e5d5611fa74d630c2ef82b0c76d29e718`, with one failing critical test and 499 passing inventory tests. The frozen independent holdout template SHA256 was `137cd6eb1573b99c4cd38f3ba70007da32708f99c28ab74c617f3142d6b74f0e`. Each final candidate passed 500 visible and three holdout tests.
- Each Python arm began at Git tree `505a7e35402692da40b176ec3096305df802b3a4`, with a failing warning/summary case. The frozen leading-space holdout SHA256 was `eb64f848392d9b85a88ca44c5e7727b5ac2b369f39a4e4cf8935b26fcd88ff6d`. Both final candidates passed 81 visible cases and the holdout.
- All arms used fresh isolated sessions and checkouts; `--safe-mode`, `--strict-mcp-config`, no Chrome or session persistence, `dontAsk` with no permission prompts, and an allowlist for Read, Edit, and the exact verifier command. Each arm made two Read calls, one Edit, and one verifier Bash call; no permission denial occurred. The prompt differed only in the verifier command. Each coding session had a 300-second timeout, 12-turn limit, and `$0.20` CLI estimated-cost cap.
- The adaptive task sent only its short goal and bounded passing-test names to OpenRouter. One Jev call was observed, resolving `typesafe/jev-1.13-20260917`, with 1,099 input tokens, 257 output tokens, 1,074 ms, and response-reported cost `$0.000046158`. No key appeared in the campaign's files. The current CLI does not enforce an account-wide spend cap; this pilot made one bounded call below the user's `$1` limit.

## Measured sessions

`Combined` adds Claude's reported fresh input, cache-write input, cache-read input, and output tokens. The four raw fields are shown so the cache contribution is visible. `USD` is Claude's list-price estimate. `Raw → shown` counts JevTO command-output bytes and is a separate diagnostic.

| Task / arm | Source + holdout | Answer review | Fresh | Cache write | Cache read | Output | Combined | USD | Wall time | Raw → shown |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rust native | Pass | Pass | 34 | 7,478 | 36,413 | 1,685 | 45,610 | 0.0270563 | 28.464 s | — |
| Rust adaptive | Pass | Pass | 34 | 4,266 | 36,851 | 2,153 | 43,304 | 0.0230161 | 33.114 s | 11,372 → 1,768 |
| Rust deterministic | Pass | Pass | 34 | 4,270 | 36,927 | 2,153 | 43,384 | 0.0230317 | 32.335 s | 11,377 → 1,773 |
| Python deterministic | Pass | Pass | 34 | 3,411 | 36,451 | 1,676 | 41,572 | 0.0188811 | 25.448 s | 2,392 → 748 |
| Python native | Pass | **Fail** | 34 | 4,219 | 36,636 | 1,953 | 42,842 | 0.0219006 | 28.341 s | — |

The Rust deterministic arm used **2,226 fewer combined tokens (4.88%)** than native; adaptive used **2,306 fewer (5.06%)**. All three Rust candidates passed the same tests and output review. JevTO's Rust arms took 3.871 and 4.650 seconds longer than native. The adaptive arm used only 80 fewer combined tokens than deterministic, while adding a Jev call and about 0.779 seconds of wall time. Adding the Jev response cost makes its estimated total `$0.023062258`, slightly above deterministic's `$0.0230317`. This pilot does not show incremental adaptive value.

The Python deterministic arm reported 1,270 fewer combined tokens than native, but **that is not an eligible verified-completion saving** under the planned answer-quality review. Native Claude incorrectly labeled `source_sha256=e04c…` from the verifier as a “JevTO capture ID,” although it never called JevTO. Its code and holdout passed; its final output was factually wrong. The deterministic arm correctly reported the warning, 81 passes, exit 0, and the receipt's actual capture ID.

## Output review and limits

An unlabeled diff review found all five edits confined to the requested source file with tests unchanged and no dependencies added. The Rust edits use `starts_with` or `strip_prefix` after trimming, matching that task's leading-space rule. Both Python edits use literal `startswith("value=")`, matching the opposite leading-space rule. Each saved `verifier-result.txt` is a completed, non-error Bash result with the expected counts and source digest. The adaptive and deterministic Rust selected results retained the 499-plus-one passing summaries, exit 0, and recall handles; the Python selected result retained the early warning and success status. For all three JevTO arms, every selected pack block matched its raw byte range and digest, the full stored digest verified, and `jevto recall --full` returned byte-exact stdout and stderr. The three final answers named the correct capture IDs.

The measured campaign's five Claude sessions summed to `$0.1138858` in list-price estimates, plus `$0.0086128` for its model preflight; these were Pro subscription usage, with no matching invoice. Earlier calibration attempts used a Windows-path permission rule that denied the JevTO verifier. Those attempts are retained under `.bench-runs/claude-coding-pilot-20260928T081337Z/` and excluded before the clean campaign. A separate alias-permission calibration is under `.bench-runs/claude-coding-pilot-20260928T081746Z/`. The interrupted Python-native calibration has no terminal usage. Completed calls across the clean and excluded work in this turn had at least `$0.2647` in list-price estimates, still not a bill.

One ordered run per condition, different verifier-command prompts, and differing cache writes prevent a causal or general savings claim. This establishes a narrow measured token reduction on one equal-quality Rust coding task, alongside a concrete answer-fidelity failure on the Python control. It does not establish lower billed cost, faster work, quality non-inferiority, or automatic Claude integration.

## Reproduce

From `repo` on Windows, with a signed-in Claude Code Pro CLI and the local JevTO executable:

```powershell
python benchmarks\claude_coding_pilot.py --self-test
python benchmarks\claude_coding_pilot.py --claude-cli C:\path\to\claude.exe --jevto-cli C:\path\to\jevto.exe
```

The runner asks for the OpenRouter key on a TTY, keeps it out of arguments and saved files, checks the Pro route and exact model, and creates a new `.bench-runs` directory. Run `python benchmarks\claude_coding_pilot.py --review-existing PATH` to recheck saved terminal, verifier, selected-block, recall, and answer evidence without another model call.
