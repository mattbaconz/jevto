# Fresh tasks: offline preparation

This set is preparation for a new native / rules / rules+Jev comparison. It contains six synthetic coding tasks: configuration precedence, empty-page pagination, atomic state updates, structured log selection, child exit/argument propagation, and a catalog API migration. Five use Python; one uses the Rust standard library. None reuses the parser task from the earlier session pilot.

Each starter passes its visible checks. A reference solution passes visible and hidden checks. Two deliberately faulty solutions per task pass visible checks but fail hidden checks. These controls check evaluator sensitivity; they do not establish agent success, task diversity sufficient for general claims, or provider savings.

## Prepare and inspect

Use an installed Python 3.10+ and Rust toolchain. Nothing is installed automatically. From the repository root:

```sh
python -B tests/fresh_tasks_smoke.py
python -B benchmarks/fresh_tasks.py --prepare .bench-runs/fresh-offline-20260930
python -B benchmarks/fresh_tasks.py --verify .bench-runs/fresh-offline-20260930
```

Preparation refuses an existing destination. The manifest records source hashes (including dirty source files), task-file hashes, a reproducible shuffled 18-entry schedule, and measurement fields. Keep the emitted manifest SHA-256 in the experiment record. Verification checks missing, changed, and added files against that manifest; the separately recorded manifest hash is the trust anchor. Keep results outside this frozen directory and copy workspaces before executing candidates.

Only `workspaces/<task>/` is agent-visible input. `evaluators/`, `controls/`, this preparator's source, and other arms' artifacts must be inaccessible to the candidate. Directory separation alone is **not** access isolation. The manifest intentionally says `isolation_verified=false`, `paid_runs_authorized=false`, and `offline_preparation_only`.

Before any future run, verify host isolation with a harmless access-denial probe; pin the exact host/model and JevTO binary; freeze prompt, tool permissions, budgets, repetitions, and order; then start fresh sessions without earlier solution context. The one-repetition schedule is a preparation placeholder, not a statistical design. Changed inputs require a new frozen export and hash. Run hidden evaluators after the candidate exits, without feeding their results back into that candidate's session.

## Comparable arms and outcomes

Native uses ordinary host tools. Rules and rules+Jev use the same host routing and permissions, with explicit deterministic and adaptive modes respectively. The adaptive arm needs a separately approved remote policy and paid budget. Every task/arm/repetition starts with an independent empty store; any cache reuse within that session is recorded. This preparator has no host launcher or provider client.

Record raw host usage, total/uncached/cached input, output/reasoning tokens when exposed, known costs and completeness, wall time, Jev latency, agent/tool retries, recalls, test results, permission denials, and timeouts. Preserve absent counters as unknown, not zero. Require a completed valid session, unchanged tests, visible and hidden passes, and no scope violation before treating an arm as successful. Report quality failures and excluded runs alongside spend; compare savings only with equivalent successful outcomes. A single fixture-control run supplies none of these agent metrics.

## Jev request accounting

New run/search-hook receipts record `jev_call_outcome` (`not_attempted`, `succeeded`, `failed`, or `cache_hit`) and an attempt marker when a send is attempted. "Attempted" means the local client entered its send path; a transport failure does not prove that the provider received or billed it. No retries were added.

`gain` and payload summaries use accounting version 1 with separate attempted, successful, failed, cached, and unknown counts. Fallback reasons are orthogonal: a valid no-evidence response is successful but falls back; a successful decision followed by a cache-write failure retains observed usage while also falling back. A valid cache hit makes no new attempt and adds no new usage or cost. An invalid cache fails closed without a remote retry.

Individually valid input/output/cost fields survive rejected response metadata or answers. Costs are summed only when finite, nonnegative, and denominated in USD. `cost_complete=false` marks unpriced attempts or ambiguous legacy outcomes. The retained `cost_usd` / `reported_cost_usd` values are known subtotals, not invoice totals; the retained `calls` field counts usage-bearing non-cache receipts, not attempts. Old receipts without enough evidence stay unknown. Advisory scope-review requests are outside these run/search-receipt aggregates.

Offline verification uses fabricated responses from a loopback HTTP server, including transport/HTTP errors, malformed responses, no-evidence fallbacks, partial usage, cache reuse and failure, and legacy records. No production key is used. Unix byte-argument preservation has a conditional hidden check; it is skipped on Windows. Existing historical benchmark results are not regenerated by these changes.
