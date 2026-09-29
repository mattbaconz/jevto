# Agent pilot on Codex CLI (gpt-6-sol, effort medium): native vs Ponytail vs JevTO

Task, repository, visible 260-test suite, and 15-case hidden holdout match the Claude pilot. Token fields are the harness's own usage report (Codex `turn.completed`; input includes cached input).

| Arm | Rep | Valid | Holdout | Visible | Impl. lines | Test runs | Test output B | Input tok | Cached in | Output tok | Time s |
| --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 1 | yes | pass | pass | 17 | 1 | 17,707 | 145,649 | 119,680 | 1,527 | 69.4 |
| ponytail | 1 | yes | pass | pass | 4 | 1 | 17,707 | 209,650 | 173,952 | 1,883 | 86.7 |
| jevto | 1 | yes | pass | pass | 15 | 1 | 350 | 134,807 | 123,008 | 1,476 | 64.6 |
| ponytail+jevto | 1 | yes | pass | pass | 5 | 2 | 718 | 222,212 | 183,808 | 1,731 | 72.2 |
| ponytail+jevto | 2 | yes | pass | pass | 11 | 1 | 359 | 194,148 | 173,824 | 1,631 | 62.2 |
| jevto | 2 | yes | pass | pass | 5 | 1 | 350 | 180,533 | 162,560 | 1,342 | 69.8 |
| ponytail | 2 | yes | pass | pass | 17 | 1 | 17,707 | 284,823 | 256,128 | 2,277 | 114.2 |
| native | 2 | yes | pass | pass | 15 | 1 | 17,707 | 242,865 | 218,752 | 2,018 | 85.0 |
| native | 3 | yes | pass | pass | 15 | 2 | 35,414 | 198,520 | 170,752 | 1,796 | 69.5 |
| ponytail | 3 | yes | pass | pass | 8 | 1 | 17,707 | 214,289 | 189,184 | 1,831 | 82.1 |
| jevto | 3 | yes | pass | pass | 13 | 1 | 350 | 194,240 | 163,968 | 1,392 | 59.6 |
| ponytail+jevto | 3 | yes | pass | pass | 18 | 1 | 359 | 273,777 | 250,624 | 2,237 | 87.6 |

## Medians of valid runs

| Arm | n valid | Holdout passes | Impl. lines | Test output B | Input tok | Uncached in | Output tok | Time s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 3 | 3/3 | 15 | 17,707 | 198,520 | 25,969 | 1,796 | 69.5 |
| ponytail | 3 | 3/3 | 8 | 17,707 | 214,289 | 28,695 | 1,883 | 86.7 |
| jevto | 3 | 3/3 | 13 | 350 | 180,533 | 17,973 | 1,392 | 64.6 |
| ponytail+jevto | 3 | 3/3 | 11 | 359 | 222,212 | 23,153 | 1,731 | 72.2 |

Invalid runs are listed and excluded from medians. Small n; indicative only.

## Means of valid runs

| Arm | Input tok | Uncached input | Output tok | Impl. lines | Shell calls | Time s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 195,678 | 25,950 | 1,780 | 15.7 | 6.3 | 74.6 |
| ponytail | 236,254 | 29,833 | 1,997 | 9.7 | 7.3 | 94.3 |
| jevto | 169,860 | 20,015 | 1,403 | 11.0 | 5.3 | 64.7 |
| ponytail+jevto | 230,046 | 27,294 | 1,866 | 11.3 | 5.7 | 74.0 |

## Reading

- Codex CLI 0.158.0-alpha.2.1, `gpt-6-sol`, reasoning effort medium, `danger-full-access` sandbox (the user's normal setting; the Windows workspace sandbox denied freshly created checkouts), user config loaded with memories disabled. The user's global RTK hook stayed active in every arm and does not rewrite `python -m unittest`.
- All 12 sessions were valid and passed both the visible suite and the hidden holdout, so outcomes were equal.
- JevTO (Codex `PreToolUse` rewrite) cut the test output reaching the model from 17,707 to about 350 bytes per run. It had the lowest input (median −9%, mean −13%), uncached input (median −31%, mean −23%), output tokens, shell calls, and time.
- Ponytail wrote the smallest code (median 8 vs 15 lines) but used more tokens and time than native on this task.
- Ponytail + JevTO had less uncached input than native (median −11%) but more total input; its runs varied widely (194k–274k).
- n = 3 per arm on one task; native ranged 146k–243k input tokens. Indicative only. No dollar figures: usage ran on a ChatGPT subscription.
