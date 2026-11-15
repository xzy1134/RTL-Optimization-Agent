# M3 acceptance: LangGraph Mock search

Date: 2026-11-15, Asia/Shanghai.

## Delivery

An actual compiled LangGraph StateGraph drives initialization, baseline scoring,
M2 strategy selection, Mock source generation, interface checking, deduplication,
M1 formal verification, M1 scoring, hard-gate evaluation, immutable records,
best promotion and final reports. Every loop passes bounded search control.
No DeepSeek transport or real LLM optimization experiment is included.

Added modules are under `agent/`, `llm/`, and `report/`, with small real-EDA test
fixtures under `examples/`. New files include `tests/test_agent.py`,
`requirements-agent.txt`, and this acceptance record. Existing edits are limited
to `.gitignore` for the local environment and `rtl_agent/README.md` for usage links.
M1/M2 source, old tests, scoring recipes, proof conditions, original RTL, course
benchmarks and external library were not modified.

The local `langgraph-fundamentals` Skill and its Python reference were read.
LangGraph 1.2.22 was installed in `.venv-m3`, using existing pip because the host
lacked ensurepip. Dependencies were not installed into system Python.

## Tests

```bash
.venv-m3/bin/python -B -m unittest discover -s rtl_agent/tests -v
```

Final result:

```text
Ran 51 tests in 9.827s
OK
```

This includes all 14 M1 and 21 M2 regressions plus 16 Agent tests. Agent coverage:

- Actual smaller equivalent candidate accepted using real M1 results.
- Wrong logic rejected with a real SAT counterexample; no synthesis follows.
- Duplicate candidate skips proof/scoring; syntax and interface errors are recorded.
- Equal-area candidate does not replace best.
- Depth violation is rejected even when area improves.
- Infeasible baseline is not published as best; a later feasible candidate can be accepted.
- NO_CHANGE and no available strategies terminate normally.
- Candidate, round, patience and runtime budgets terminate.
- Cooperative Mock timeout consumes remaining budget and records a failure.
- UNKNOWN does not enter synthesis or best.
- Later failure cannot overwrite an accepted version.
- Source remains unchanged; round records, diffs, logs, final output and best manifest exist.
- Evaluator rejects missing metrics, mismatched fingerprints and non-PASS proofs.

All candidate proof/PPA conclusions in integration tests are obtained from
Yosys. Small dictionary-only evaluator tests exercise invalid-result gates;
they are not represented as hardware measurements. Runtime tests use a tiny
startup budget or a sleeping Mock client, not fake EDA metrics.

## Reproduced search demonstration

```bash
.venv-m3/bin/python -B -m rtl_agent.agent \
  rtl_agent/examples/mock_baseline.v --top t \
  --responses rtl_agent/examples/mock_sequence.json \
  --depth-limit 6 --patience 8
```

Actual final demonstration:

- Run: `work/rtl_agent/mock-search-i__6imis/`.
- Baseline: 3-bit dual multiplier; area 75.278, logic depth 6.
- Best: area 42.560, logic depth 6; area reduction 43.4629%.
- Five generated candidates, two candidate formal PASS results, six model rounds.
- Stop reason: `NO_CHANGE`. Recorded runtime: 0.935913 seconds on this host.
- Usage mode: Mock, zero real API calls and zero real tokens.

| Candidate | Description | Proof | Area | Depth | Accepted | Reason |
|---|---|---|---:|---:|---|---|
| 0001 | Same hardware, different source | PASS | 75.278 | 6 | No | NO_AREA_IMPROVEMENT |
| 0002 | Incorrect product | FAIL | unavailable | unavailable | No | FORMAL_FAIL |
| 0003 | Malformed RTL | NOT_RUN | unavailable | unavailable | No | PRECHECK_ERROR |
| 0004 | Shared multiplier | PASS | 42.560 | 6 | Yes | ACCEPTED |
| 0005 | Exact repeat of 0004 | NOT_RUN | unavailable | unavailable | No | DUPLICATE |

Round 0006 contains NO_CHANGE and has no generated RTL. Requested strategy and
returned strategy are recorded separately. Candidate 0004 reports the actual
resource-sharing strategy; Mock does not have to return the primary suggestion
if it chooses another admitted strategy.

Best source: `work/rtl_agent/mock-search-i__6imis/round-0004/candidate.v`.
Manifest: `best.json`. Full table: `report.md`. Machine-readable summary:
`report.json`. Each `round-NNNN/record.json` links its real tool run directories.

## Independent depth-gate evidence

`test_real_depth_violation_does_not_replace_feasible_baseline` synthesizes the
`mock_priority.v` original to area 64.372 / depth 5. It proves `mock_shared.v`
equivalent and measures area 42.560 / depth 6. With the fixed limit 5 the graph
rejects it as DEPTH_LIMIT and retains the feasible original. No artificial delay
number or modified scoring script is used.

## Limits and next stage

The demonstrated circuits are explicit small integration fixtures, not evidence
of general optimization performance or Hidden Case success. Strict combination
proof is unchanged; FSM remains disabled. Timing is logic_depth, not STA or ns.
Exact source dedup does not identify all semantically equivalent RTL. There is
no checkpoint resume or multi-Agent execution. Budget enforcement covers EDA
processes and cooperative model timeouts; a custom client must honor its timeout.

If no depth limit is set, reports explicitly mark that upper-bound checking was
omitted. If no feasible best exists, original RTL is a labelled fallback only.

M4 should implement the existing ModelClient protocol with DeepSeek credentials,
bounded HTTP timeouts/retries, strict response parsing and actual usage
accumulation, without changing the acceptance gates. Current token fields are
Mock placeholders, not a completed real-provider accounting implementation.
