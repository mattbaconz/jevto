# Claude Code pilot: native vs Ponytail vs JevTO

Model `claude-haiku-4-5-20251001`, Claude Code with `--setting-sources project,local` (user hooks, plugins, and output style excluded). Task: implement `parse_duration` and verify with the full 260-test `unittest -v` suite. Holdout: 15 hidden cases. Costs are Claude CLI list-price estimates under a subscription, not a bill.

| Arm | Rep | Holdout | Visible | Impl. lines | Test runs | Test output B seen | Turns | Output tok | Combined tok | Est. $ | Time s |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 1 | FAIL | pass | 34 | 1 | 17,705 | 7 | 3,186 | 120,494 | 0.0597 | 51.6 |
| ponytail | 1 | FAIL | pass | 21 | 1 | 17,705 | 9 | 2,866 | 164,012 | 0.0649 | 49.2 |
| jevto | 1 | FAIL | pass | 31 | 1 | 349 | 8 | 2,521 | 127,478 | 0.0437 | 43.7 |
| ponytail+jevto | 1 | FAIL | pass | 23 | 1 | 358 | 6 | 3,586 | 107,819 | 0.0509 | 51.2 |
| ponytail+jevto | 2 | FAIL | pass | 24 | 1 | 358 | 6 | 3,412 | 91,875 | 0.0486 | 46.2 |
| jevto | 2 | FAIL | pass | 29 | 1 | 349 | 6 | 3,445 | 83,626 | 0.0447 | 57.9 |
| ponytail | 2 | FAIL | pass | 32 | 1 | 17,705 | 6 | 6,456 | 122,748 | 0.0844 | 73.9 |
| native | 2 | FAIL | pass | 30 | 1 | 17,705 | 10 | 5,039 | 177,078 | 0.0787 | 64.6 |
| native | 3 | FAIL | pass | 27 | 1 | 17,705 | 7 | 2,961 | 119,257 | 0.0579 | 43.9 |
| ponytail | 3 | FAIL | pass | 27 | 1 | 17,705 | 7 | 1,972 | 125,662 | 0.0543 | 37.8 |
| jevto | 3 | FAIL | pass | 35 | 1 | 349 | 8 | 3,410 | 116,584 | 0.0483 | 75.4 |
| ponytail+jevto | 3 | FAIL | pass | 27 | 2 | 1,950 | 12 | 4,191 | 219,651 | 0.0690 | 85.6 |

## Medians by arm

| Arm | n | Holdout passes | Impl. lines | Test output B | Output tok | Combined tok | Est. $ | Time s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 3 | 0/3 | 30 | 17,705 | 3,186 | 120,494 | 0.0597 | 51.6 |
| ponytail | 3 | 0/3 | 27 | 17,705 | 2,866 | 125,662 | 0.0649 | 49.2 |
| jevto | 3 | 0/3 | 31 | 349 | 3,410 | 116,584 | 0.0447 | 57.9 |
| ponytail+jevto | 3 | 0/3 | 24 | 358 | 3,586 | 107,819 | 0.0509 | 51.2 |

Invalid or failing arms are reported, not dropped. With n this small, differences are indicative only.
## Means by arm

| Arm | Combined tok | Output tok | Cache-write tok | Est. $ | Impl. lines | Time s | Turns |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 138,943 | 3,729 | 17,499 | 0.0655 | 30.3 | 53.4 | 8.0 |
| ponytail | 137,474 | 3,765 | 18,761 | 0.0679 | 26.7 | 53.6 | 7.3 |
| jevto | 109,229 | 3,125 | 10,161 | 0.0456 | 31.7 | 59.0 | 7.3 |
| ponytail+jevto | 139,782 | 3,730 | 12,548 | 0.0562 | 24.7 | 61.0 | 8.0 |

## Reading

- Outcomes were equal across arms: every session passed the visible suite, and every session failed the same strict holdout rule ("single spaces only": `"1h  30m"` was accepted). Two sessions (one Ponytail, one Ponytail + JevTO) also accepted `"5 m"`. No arm changed tests.
- JevTO cut the test output the model read from about 17.7 KB to about 350 bytes per run. Its sessions wrote about 40% fewer cache tokens, and its estimated cost was the lowest (mean −30%, median −25% versus native).
- Ponytail produced the smallest code (median 27 vs 30 lines alone, 24 with JevTO) but did not reduce session tokens or cost on this task; its injected instructions add input.
- Ponytail + JevTO had the smallest code and a median of 107,819 combined tokens (−11%), but one run needed a second test pass and 12 turns (219,651 tokens), which pulled its mean above native.
- n = 3 per arm on one small task with Haiku 4.5; native varied from 119k to 177k combined tokens. Treat differences as indicative, not significant. An earlier run of this harness was discarded because Claude denied Edit/Write in every session (`dontAsk` mode); this run used `acceptEdits` and recorded zero denials.
