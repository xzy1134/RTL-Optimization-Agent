# M4 acceptance: DeepSeek client and bounded search

Date: 2026-11-16, Asia/Shanghai.

## Implemented

- Added `llm/deepseek_client.py`: standard-library HTTPS chat completions,
  environment-only credentials, configurable model/endpoint, JSON output mode,
  non-thinking mode, strict response parsing, finite retries and request pacing.
- Extended `llm/client.py` with safe ModelError accounting and provider artifacts.
- Extended Agent CLI with Mock/DeepSeek selection. Mock remains the default.
- Extended State budgets with HTTP-attempt and token limits. Retries share the
  same request allowance as normal calls. Timeout/network ambiguity and missing
  usage cannot silently trigger unlimited requests.
- Added actual provider usage aggregation, API durations, unknown-cost tracking,
  sanitized attempt artifacts and model-independent final reports.
- Reviewed the existing structured prompts and added explicit JSON examples.
  Failed-candidate feedback now includes measured area/depth and the depth limit.

M1 synthesis, verification, scoring and original RTL/benchmarks are unchanged.
No additional Python dependencies were installed. No Git commit or push was made.

## Tests: offline provider tests and real EDA regression

```bash
.venv-m3/bin/python -B -m unittest discover -s rtl_agent/tests -v
```

```text
Ran 72 tests in 11.373s
OK
```

All 54 pre-M4 tests passed, plus 18 new tests. New coverage includes correct
Authorization/payload construction, candidate/no-change parsing, rejected
malformed JSON and unknown strategies, truncated/empty responses, missing usage,
HTTP 429 retry, bounded 5xx retries, authentication redaction, timeout handling,
Retry-After deadlines, token preflight and request budgets. Graph integration
tests preserve failure usage, feed errors to the next round, and stop on missing
usage or exhausted request allowance.

The provider tests replace HTTP with deterministic response fixtures; their
token values and HTTP counters are test data, not a live DeepSeek measurement.
Their candidate equivalence and area/depth conclusions use real M1 Yosys calls.

## Mock CLI smoke test

```bash
.venv-m3/bin/python -B -m rtl_agent.agent \
  rtl_agent/examples/mock_baseline.v --top t \
  --responses rtl_agent/examples/mock_sequence.json --depth-limit 6 --patience 8
```

Actual artifacts: `work/rtl_agent/mock-search-jxbcun8v/`.
Outcome IMPROVED, stop NO_CHANGE. Baseline area/depth 75.278 / 6; best 42.560 / 6.
Runtime 1.014500 seconds. Real API calls and tokens: 0. This validates backward
compatibility, not autonomous model discovery.

## Initial agent-side live preflight: blocked before any request

After regression tests passed, the following budgeted CLI was invoked:

```bash
.venv-m3/bin/python -B -m rtl_agent.agent \
  rtl_agent/examples/mock_baseline.v --top t \
  --backend deepseek --model deepseek-flash \
  --depth-limit 6 --max-candidates 5 --max-rounds 5 --patience 5 \
  --max-api-requests 5 --max-tokens 50000 --max-output-tokens 2048 \
  --runtime 300 --max-retries 1
```

The process did not inherit DEEPSEEK_API_KEY. CLI exited with code 2 before
creating the client or sending a request. No key was printed, read from shell
history, or requested in chat. The reported inability to read an environment
variable does not imply the user's key is invalid or unavailable in another WSL
terminal.

Live DeepSeek calls: 0. Live input/output tokens: 0. No live candidates, API
latency or model optimization improvement were measured. Autonomous discovery
of resource sharing remains unverified. Run the documented command in the same
local terminal that has the exported key; no source change is needed.

## Subsequent live experiment: completed from the user's WSL terminal

The user executed the bounded command in their configured terminal. The actual
artifacts were inspected at `work/rtl_agent/deepseek-search-sobk2xmi/`.
Unlike the offline HTTP fixtures, these records contain actual DeepSeek API
responses and provider usage. No additional API request was made for this review.

| Round | Strategy | Formal | Area | Logic depth | Outcome |
|---|---|---|---:|---:|---|
| 1 | resource_sharing | PASS | 42.560 | 6 | Accepted |
| 2 | mux_optimization | PASS | 75.278 | 6 | No improvement over best |
| 3 | resource_sharing | NOT_RUN | unavailable | unavailable | Exact duplicate |
| 4 | arithmetic_restructuring | NOT_RUN | unavailable | unavailable | NO_CHANGE |

Baseline area/depth: 75.278 / 6. Best area/depth: 42.560 / 6. Area reduction:
43.4629%. The best candidate selects operands first and performs one multiply,
instead of computing both products then selecting the result. It was generated
by DeepSeek using the supplied strategy guidance, not supplied by Mock.

There were 3 candidate responses and 2 candidate formal PASS results. All 4
API calls completed without retries or missing usage. Actual provider input
tokens: 13,146; output tokens: 867; total tokens: 14,013. Recorded API time:
7.785236 seconds; total search runtime: 8.534672 seconds. Stop reason: NO_CHANGE.

Best RTL: `round-0001/candidate.v` under that run. `best.json` agrees with the
candidate record. The baseline, best and candidate synthesis fingerprints match,
and best depth 6 meets the configured bound 6. Original RTL was not changed.

This establishes one real-model, tool-validated optimization trajectory on the
small fixture. It does not establish general benchmark performance. Follow-up:
reduce repeated attempts after baseline restarts and provide compact summaries
of tried transformations; dedup saves EDA work but does not refund generation
tokens.

## Limits and next work

Token totals are provider measurements when usage exists. Unknown requests are
not considered free: separate unknown counts and conservative budget reservations
are recorded, and summary token_usage_complete becomes false. Reservations use
request UTF-8 bytes plus framing allowance and capped output tokens, not an exact
tokenizer or guaranteed invoice cap. See `llm/README.md` for timing limitations.

Prompt refinements must be guided by the first real run: inspect JSON compliance,
equivalence pass rate, repeated candidates, area/depth tradeoffs and token cost.
Then reduce redundant RTL/strategy text and tune feedback/strategy ordering one
factor at a time. Current proof is still combinational, FSM is disabled, and
logic_depth is not ns or STA.

Provider behavior references:

- [Chat completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [JSON mode](https://api-docs.deepseek.com/guides/json_mode/)
- [Error codes](https://api-docs.deepseek.com/quick_start/error_codes/)
