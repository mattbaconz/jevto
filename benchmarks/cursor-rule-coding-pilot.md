# Cursor project-rule coding pilot — 2026-09-28 local time

This exploratory edit-and-verify pair tested whether Cursor's project-rule route could carry a coding task through JevTO. It used Cursor CLI `2026.09.26-dd393fe`, requested the exact model ID `grok-4.7-xhigh`, and both completed streams reported `Grok 4.7 256K Extra High`. No Fast or substitute model was used. Each arm had a fresh tracked Git checkout, the same initial application tree SHA256 `0ff680a80a093a926c7da82c9d6704df53086b0858e87fff48a434a49cc09a55`, the same task prompt, and the same frozen holdout SHA256 `eb64f848392d9b85a88ca44c5e7727b5ac2b369f39a4e4cf8935b26fcd88ff6d`. The only planned difference was a committed, always-applied project rule for the verifier: native `python verify_once.py` or JevTO's exact-command wrapper. Both arms disabled the same seven project MCP servers, used `--force --trust --sandbox disabled` on Windows, and ran one verifier after editing. These settings are part of this test, not a general permission claim.

The task asked `summarize.total` to sum only lines **beginning** with `value=` and to ignore warnings and misleading notes. The visible verifier printed 80 generated cases plus a summary case. The independent frozen holdout included a leading-space line that must be ignored. The holdout was strengthened after an earlier, separate explicit-wrapper coding pilot and was frozen **before** these two completed project-rule arms. `cursor_rule_coding_pair.py` retains the fixture setup, prompt, rule bytes, model preflight, terminal stream, diff, independent verification, receipt, exact-recall check, and rule cleanup. The raw artifacts are under ignored `.bench-runs/cursor-rule-coding-native_rule-20260927T220719Z/` and `.bench-runs/cursor-rule-coding-jevto_rule-20260927T221150Z/`.

| Observation | Native project rule | JevTO project rule |
| --- | ---: | ---: |
| Final source | Checks `line.startswith("value=")` | Strips whitespace, then checks the prefix |
| Visible verifier | 81 lines passed, exit 0 | 81 lines passed, child exit 0 |
| Frozen holdout | Passed | **Failed**: counted `" value=7"` |
| Final source marker | Matched | Matched |
| Verifier shell calls | 1 | 1, through JevTO |
| CLI terminal result | Success | Success |
| Cursor-reported input tokens | 66,542 | 120,060 |
| Cursor-reported output tokens | 7,254 | 3,935 |
| Cursor-reported cache-read tokens | 203,776 | 87,040 |
| Cursor-reported cache-write tokens | 0 | 0 |
| Combined token count, derived from four CLI fields | 277,572 | 211,035 |
| Elapsed wall time | 230.587 s | 184.445 s |
| Independently verified completion | **Yes** | **No** |

The JevTO receipt recorded one capture, 2,392 raw bytes, 1,040 delivered bytes, and child exit 0. Its capture ID appeared in the completed tool result and final answer. Local full recall matched a fresh native verifier run on the **same final source** byte for byte on stdout and stderr. The installed rule was unchanged during the turn and was removed through JevTO's ownership record afterward. This confirms the narrow routing, capture, and recall path. It does not rescue the candidate's failed holdout.

The JevTO arm reported **53,518 more input tokens** and completed **46.142 seconds sooner**, but it did not complete the task correctly. Summing the four reported fields gives **66,537 fewer combined tokens** for that arm, driven by a much lower cache-read count. The CLI did not emit a `totalTokens` field; the sum follows [Cursor's SDK definition of total tokens](https://cursor.com/docs/sdk/typescript#token-usage), which uses the same field names. The CLI-to-SDK equivalence is an inference, and these counts are not a provider bill. Neither the input difference nor the derived combined difference is a token benefit or causal estimate for a verified task. The two rule texts necessarily differ, cache behavior differs substantially, model behavior is stochastic, and this is one ordered pair. The incorrect edit was made before either verifier result was delivered, so the saved trace does not show that JevTO's selected output caused it. It does show why correctness must gate any savings claim.

Before the completed native arm, an attempt with the same pinned model was rejected by the host before any stream event: `Cannot use this model: grok-4.7-xhigh. Available models:` with no listed models. It produced no candidate, receipt, or usage and is excluded. A direct same-workspace no-tool control then returned `READY` on exact xhigh, and the fresh native retry completed. The rejected attempt is retained at `.bench-runs/cursor-rule-coding-native_rule-20260927T220315Z/`; it is a transient host observation, not a failed coding candidate.

This pilot leaves the full JevTO token, cost, speed, and quality claim unproven. It also does not prove that JevTO caused the wrong edit. The route is an agent-followed instruction for one named verifier, not automatic interception. No OpenRouter Jev call was made in these arms.

To reproduce one arm with a reviewed local CLI and JevTO binary, run the following from `repo`; each invocation creates a fresh ignored artifact directory. Use the same exact paths for both arms and inspect the two manifests before comparison:

```powershell
python benchmarks\cursor_rule_coding_pair.py --arm native_rule --cursor-cli C:\path\to\cursor-agent.ps1 --jevto C:\path\to\jevto.exe --timeout-seconds 300
python benchmarks\cursor_rule_coding_pair.py --arm jevto_rule --cursor-cli C:\path\to\cursor-agent.ps1 --jevto C:\path\to\jevto.exe --timeout-seconds 300
```
