# Codex pre-hook token pair — 2026-09-27

This is an exploratory **Codex** host check on a synthetic one-command task. It is separate from the Cursor pilot, which remains pinned to `grok-4.7-xhigh` Extra High. No Grok Fast, OpenRouter, or Jev request was used here. Raw JSONL traces and the manifest are local under ignored `.bench-runs/codex-prehook-pair-20260927T130352Z/`.

## Frozen setup for this pair

- Codex desktop CLI `0.158.0-alpha.2.1`, SHA256 `8f0554ede25bbc5450921897c468b2e84635aa513c5017457997af0954581f49`; model `gpt-6-luna`; `workspace-write` sandbox; fresh ephemeral session per run.
- JevTO debug executable SHA256 `aeb5b5ce6bc4196493223731acf83daf3d0bacfb3fa68523b60c409ba619ce98`.
- Two disposable Git fixtures with the same source SHA256 `ded31a8326b397dc58a7efb5234bf59de32a427f9376413cac4550f77ebe9c84`: 500 passing Rust tests. Each fixture was compiled before measurement. The hook fixture had only JevTO's project `PreToolUse` hook. Both arms used `--dangerously-bypass-hook-trust` for unattended automation of the reviewed local hook; no sandbox-bypass or approval-bypass flag was set.
- Identical prompt in every run: run `cargo test --workspace` once, then report passing/failing counts and any capture ID. Order: native, hook, hook, native. No file reads or extra shell calls appeared in the traces.
- The harness and exact prompt are in [`codex_pre_hook_pair.py`](codex_pre_hook_pair.py). Reproduce with `python benchmarks/codex_pre_hook_pair.py --codex-cli C:\path\to\codex.exe --pairs 2` after building the JevTO debug binary. The script removes the installed project hook in `finally` and leaves ignored captures for inspection.

## Observations

| Order | Arm | Provider input tokens | Cached input tokens | Provider output tokens | Tool result bytes | Elapsed seconds | Verified outcome |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | Native | 57,127 | 48,640 | 611 | 10,815 | 39.97 | 500 passed, 0 failed |
| 2 | JevTO pre-hook | 53,735 | 48,640 | 436 | 1,225 | 31.23 | 500 passed, 0 failed; capture `0cbb2e41-b107-4f0e-a349-9e62da62f817` |
| 3 | JevTO pre-hook | 53,661 | 37,376 | 360 | 1,225 | 32.34 | 500 passed, 0 failed; capture `50aae366-2814-4f92-87b0-3bee0bb3834d` |
| 4 | Native | 56,839 | 37,376 | 309 | 10,815 | 28.44 | 500 passed, 0 failed |

Each hook capture contained 10,620 stdout bytes plus 195 stderr bytes; the selected result retained the passing summary and exact recall command. In both hook runs, the agent's final answer contained the capture ID shown by the tool. The script required exactly one completed shell call, a successful 500-test result, and one provider usage event for each run. Afterward, `hooks.json` had an empty `PreToolUse` list, the two captures remained in the ignored project store, and no supplied OpenRouter key string appeared in the traces or script.

The two matched comparisons show **3,392 and 3,178 fewer provider-reported input tokens** with JevTO. Across the four sessions, the native arms used 113,966 input tokens and the JevTO arms used 107,396: **6,570 fewer input tokens (5.76%)** for this synthetic task. Cached input counts matched within each pair. These are observed session usage values, so they include hook instructions, the shell result, and the rest of Codex's context; they are more informative than byte counts alone.

This task has no code edit, no recall, and no difficult decision after the test. Four runs cannot establish a general saving, cost reduction, latency improvement, or unchanged quality on real work. The provider did not expose a matched bill. The measured path covers only the tested Windows PowerShell `cargo test` pre-hook on this Codex version; it says nothing about native Cursor interception or Claude access. A larger preregistered task study remains required for the product's broad claim.
