# Serial LangGraph RTL search

This stage joins the unchanged M1 EDA interfaces and M2 strategy library using
an actual compiled LangGraph `StateGraph`. Mock supplies source text only. All
formal and synthesis results come from real Yosys runs. M4 also supports the
DeepSeek backend; see [provider usage and budgets](../llm/README.md). Mock remains
the default and uses no real model tokens.

## Environment

The development host did not have LangGraph or `ensurepip`. A project virtual
environment was created with system site packages, then system pip installed
LangGraph into that environment. System Python was not changed. The environment
is excluded from Git.

```bash
python3 -m venv --without-pip --system-site-packages .venv-m3
python3 -m pip --python .venv-m3/bin/python install -r rtl_agent/requirements-agent.txt
```

Tested LangGraph version: 1.2.22, Python 3.14.4, PyYAML 6.0.3, Yosys 0.52,
Nangate45. The optional Agent dependencies are separate from M1/M2. M1 and M2
remain usable without importing LangGraph. Use the virtual environment for the
combined test suite:

```bash
.venv-m3/bin/python -B -m unittest discover -s rtl_agent/tests -v
```

## Reproduce the Mock demonstration

```bash
.venv-m3/bin/python -B -m rtl_agent.agent \
  rtl_agent/examples/mock_baseline.v --top t \
  --responses rtl_agent/examples/mock_sequence.json \
  --depth-limit 6 --patience 8
```

The baseline is a small 3-bit dual-multiplier fixture, not a modified course
benchmark. The sequence contains a source-only change, a wrong function, syntax
error, shared multiplier, duplicate shared multiplier, and `NO_CHANGE`. The
baseline and all candidate RTLs remain separate files. The separate
`mock_priority.v` fixture is used to test a real depth violation: it maps to
64.372 area / depth 5, while `mock_shared.v` maps to 42.560 / depth 6. The latter
passes formal verification but must lose admission under a limit of 5.
`mock_shiftadd.v` is another measured, equivalent implementation, not a promised
improvement for arbitrary widths or libraries.

JSON response files accept `action`, `strategy_id`, `description`, and either
`rtl` or `rtl_file`. File paths are resolved relative to the JSON file. Omitted
strategy IDs in Mock are filled from the requested primary strategy. An
exhausted sequence returns `NO_CHANGE`. No script supplied by the model is run.

CLI budgets: `--max-candidates`, `--max-rounds`, `--patience`, `--runtime`, and
`--eda-timeout`. `--source` accepts repeatable fixed auxiliary sources. If
neither `--depth-limit` nor `--depth-limit-baseline` is supplied, no depth upper
bound is enforced; the report says so. These flags are mutually exclusive.
`--depth-limit-baseline` first measures the original with the same EDA config
and fixes that depth as the search limit. This extra preparation synthesis is
outside the search runtime budget; failure stops before any model request.
The root `./run_agent.sh` menu uses this mode for user-selected RTL files and
uses real DeepSeek directly. Enter a file path and its top module name. Mock
remains available through the full CLI with a matching response JSON.
Logic depth is in logic levels, not ns. CLI also accepts `--tools-env` and
`--outdir`. Invalid input/configuration can raise a startup error; candidate
parse/proof/synthesis failures are recorded and do not crash the search.

## Graph and State

```text
START -> initialize -> baseline -> controller
controller -> select_strategy OR final_report
select_strategy -> generate OR final_report
generate -> precheck OR record
precheck -> verify OR record
verify -> synthesize_evaluate OR record
synthesize_evaluate -> record -> controller
final_report -> END
```

- `initialize`: validate budgets, snapshot original sources, freeze Liberty.
- `baseline`: run M1 synthesis and self-equivalence; publish baseline best only
  if proof passes and the configured depth constraint is met.
- `controller`: enforce time, candidate, round and no-improvement budgets.
- `select_strategy`: re-analyze the parent using M2 and rotate through reference
  suggestions by attempt count then M2 priority. With no suggestions, use open
  exploration. Disabled FSM stays out of the suggestions; sequential proof is
  still unsupported and cannot be enabled by choosing a label.
- `generate`: pass original/parent RTL, strategy descriptions/evidence, metrics,
  last five failure summaries and forbidden changes to Mock. Save request,
  response, RTL and actual diff.
- `precheck`: parse with M2, compare the interface with M1 rules (including
  parameter defaults), and deduplicate exact source-bundle hashes.
- `verify`: compare every unique valid candidate to the immutable original using
  M1 SAT, not merely to the parent. Only PASS proceeds.
- `synthesize_evaluate`: run unchanged M1 scoring and apply hard gates.
- `record`: save an exclusive candidate record, then atomically publish best.
- `final_report`: save JSON and Markdown summaries and an output RTL path.

Nodes return partial updates without mutating State. This serial graph uses
explicit replacement of small lists/maps rather than append reducers. State
stores paths/IDs, hashes, fixed configuration, candidate counters, strategy
recommendations, current result, best pointer, budget, usage placeholders and
five bounded trial summaries covering success and failure. Full RTL, requests,
diffs and logs stay on disk; the best RTL is also sent when it differs from the
original and current parent. The model receives baseline/best notes even on a
baseline restart. See [memory acceptance](../MEMORY_ACCEPTANCE.md).
The history index has one path per round and is bounded by the configured limit.

One completed model attempt is one search round. `NO_CHANGE` counts as a
non-improving round, not a generated candidate, and does not end search by
itself. The next request asks for open exploration with the notebook and rotated
suggestions. Patience, round/request/token/time limits still end search.
Candidate limits include
duplicates and malformed CANDIDATE responses. Each unsuccessful round increases
patience usage; accepted candidates reset it. Strategies rotate rather than
always retrying the top strategy. Parents use legal best; every third attempt
may restart from the original. A failed candidate is never a parent.

## Acceptance rules

Area is compared only after formal PASS, successful complete mapped metrics,
matching synthesis fingerprint, and depth compliance. Strictly smaller area
updates a legal best. Equal area is not promoted. With no legal best, the first
feasible candidate establishes one, even if its area exceeds an infeasible
baseline; reports distinguish feasibility from positive area improvement.

The library is copied once at initialization. M1 fingerprints still cover the
same tool version, recipe, library contents, top and metric. EDA timeouts are
clamped to the remaining runtime, which does not alter the scoring recipe.
Initial self-proof prevents unsupported input from being advertised as legal
best. UNKNOWN, ERROR and FAIL cannot enter the valid pool. A baseline that fails
the depth bound has no best pointer until a feasible candidate is found. If no
feasible result exists, original RTL is returned only as an explicitly labelled
fallback (`NO_FEASIBLE_DESIGN`).

Exact dedup uses top-source plus ordered auxiliary-source hashes in one fixed
experiment. It avoids repeat formal/synthesis runs, but is not semantic dedup.
Duplicate valid candidates still undergo the cheap interface precheck. M1
rejects unsupported cells and X/Z as before; the Agent does not change these
conditions. No model output can modify the library, tool recipe or constraints.

## Artifacts

Each `work/rtl_agent/mock-search-*` contains:

- `experiment.json`, original source manifests, fixed library, baseline EDA logs.
- `round-NNNN/`: exclusive request/response, candidate RTL, `change.diff`, tool
  run directories, `record.json`. NO_CHANGE has a round record but no RTL.
- `best.json`: atomic manifest pointing at original or an immutable accepted
  candidate, with metrics and proof reference. It is absent if no legal best exists.
- `report.json` / `report.md`: baseline and best metrics, feasibility, improvement,
  formal status, candidate/PASS counts, runtime, costs and stop reason.

Rejected candidates have unavailable metrics as null/absent, never zero-valued
fake scores. A syntax error has `PRECHECK_ERROR` and proof `NOT_RUN`, not a false
claim of formal inequivalence. A duplicate has proof `NOT_RUN`. Records also
contain measurement fingerprints, parent/strategy IDs and run paths. Normal
execution does not overwrite an earlier round or candidate record.

## Model interface and M4 boundary

`llm/client.py` defines `ModelClient.generate(request, timeout=...)`,
`ModelRequest` and `ModelResponse`. No provider SDK is required. Mock is a finite
replay client and can simulate timeout; it never returns measurements.

`DeepSeekClient` now implements this interface with environment-only credentials,
strict JSON parsing, bounded retries and actual usage accounting. `--backend
deepseek` selects it. Mock usage remains simulated zero. SearchBudget adds
`max_api_requests` (all HTTP attempts) and `max_tokens` (known usage plus reserved
unknown usage). Details and limitations are documented in the provider README.

Remaining runtime is checked at graph boundaries and enforced on EDA processes.
Clients must honor the supplied timeout; arbitrary custom Python clients that
ignore it are not forcibly killed. Runtime includes initialization, model calls,
EDA, control and report generation, measured just before final JSON serialization.
Small final artifact-write overhead can exceed a budget. There is no checkpoint
resume, concurrency, sequential proof, real STA or learned policy. Historical M3
acceptance measurements remain Mock-only.

Graph construction follows the local `langgraph-fundamentals` Skill and the
[official Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
