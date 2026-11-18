# Cross-round memory and existing benchmark checks

Date: 2026-11-16, Asia/Shanghai.

## Change

Each new model request now includes a tool-derived experiment notebook:

- `feedback` includes the last five successful and unsuccessful rounds.
- `memory.baseline` records the original metrics and proof status.
- `memory.best` records the best design, measured metrics, strategy and actual
  edit excerpt. If a baseline restart differs from best, best RTL is included.
- `memory.recent_trials_ref` points to feedback rather than sending it twice.
- Trial summaries distinguish model-reported intent from actual source diff,
  proof, measured metrics, acceptance and failure reason. Older records remain
  on disk, while request context is bounded and reports omissions.

Long generic proof/precheck log tails and ASCII banners are no longer feedback.
Syntax failures retain a short actual error excerpt. The raw logs remain intact.
Best and baseline are remembered even when older trials leave the five-item
window. Initial strategy selection and the every-third-attempt baseline restart
remain unchanged. This change supplies context; it cannot guarantee that a model
will never repeat a design. Existing hard acceptance gates and exact dedup remain.

## Existing benchmark exercise

`python -m rtl_agent.experiments.bench_memory --backend mock` runs popcount3,
addsubz and barrel from `case/bench`. Source files are not edited. Each case's
depth limit is its measured M1 baseline depth, not a claimed official constraint.
All formal and synthesis results are real. Mock uses existing alternate RTL for
popcount3/addsubz and a hand-prepared equivalent shift expression for barrel.
These are not DeepSeek-generated results or a memory effectiveness comparison.

Artifacts inspected: `work/rtl_agent/bench-memory-zasshw0t/summary.json`.

| Case | Baseline area/depth | Candidate formal | Candidate area/depth | Outcome |
|---|---|---|---|---|
| popcount3 | 6.384 / 2 | PASS | 5.586 / 2 | Accepted |
| addsubz | 75.544 / 10 | PASS | 73.948 / 11 | Rejected by depth limit |
| barrel | 39.368 / 4 | PASS | 38.570 / 4 | Accepted |

The second model request in each offline case contains the first round's actual
result. Mock API calls/tokens are zero. Suite runtime was 2.117927 seconds.

## Live benchmark command

The current execution environment does not inherit DEEPSEEK_API_KEY. To run
actual DeepSeek on these cases, use the same WSL terminal where the key is
exported:

```bash
.venv-m3/bin/python -B -m rtl_agent.experiments.bench_memory --backend deepseek
```

For one case, add `--case popcount3`, `--case addsubz` or `--case barrel`.
Each case permits at most 3 candidates, 3 rounds, 3 HTTP requests, 30,000 budget
tokens and 120 search seconds; retries are disabled. All three cases therefore
permit at most 9 HTTP requests and 90,000 budget tokens in total. Baseline
premeasurement adds EDA time outside each search's runtime budget. These are
upper limits, not actual measured token use. No key belongs in CLI arguments or
chat. Default backend is Mock to avoid accidental spending.

## Tests and limits

New tests exercise successful feedback, failed feedback, baseline restarts with
best RTL retained, bounded notes, no invented best, actual error extraction and
all three benchmarks with real M1. Original circuit hashes are checked before
and after the benchmark run. Full command:

```bash
.venv-m3/bin/python -B -m unittest discover -s rtl_agent/tests -v
```

Final result: 77 tests passed in 14.609 seconds (72 existing tests and 5 new
memory/benchmark tests). A separate DeepSeek suite preflight exited with code 2
because the process did not inherit the key; no live request was made.

No M1/M2 scoring/proof logic, original RTL, library or constraint was modified.
No checkpoint recovery, cross-experiment memory or vector database is added.
See FOLLOW_UP.md for the user's later vector-store evaluation reminder.

## First user-run DeepSeek benchmark suite

Actual artifacts: `work/rtl_agent/bench-memory-ng2rtla5/summary.json`.
The three cases used 6 HTTP calls and 25,144 total provider tokens in 14.324958
seconds, without additional API calls during review.

- popcount3: baseline 6.384 / 2; accepted Boolean arithmetic structure 5.586 / 2.
- addsubz: model returned NO_CHANGE; baseline 75.544 / 10 retained.
- barrel: candidate measured 47.348 / 6 and exceeded bound 4; baseline 39.368 / 4
  retained. The next model response cited the measured failed attempt.

The final popcount3 response discussed prior successful/unsuccessful trials, but
omitted `rtl: null` in its NO_CHANGE object. Strict parsing recorded RESPONSE_FORMAT;
the request budget then stopped the run. This is a protocol failure, not a new
RTL failure. These results show notes were used in explanations, not a controlled
proof of token savings or search quality. In particular NO_CHANGE does not prove
that no better structure exists.

`./run_agent.sh` now provides a short menu for benchmark/demo and Mock/DeepSeek.
It does not change credential loading. Default selection is offline Mock. Four
launcher tests use a fake interpreter and never spend API tokens. The combined
suite now passes 81 tests in 14.958 seconds.
