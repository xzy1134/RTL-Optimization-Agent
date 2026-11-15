# Strategy library stage acceptance

Date: 2026-11-15, Asia/Shanghai.

## Scope

This stage adds six YAML strategies, strict schema validation, deterministic
selection, generic-netlist analysis and an M1 synthesis-artifact reader. It does
not implement LangGraph, DeepSeek, candidate generation or an optimization loop.

The host already had PyYAML 6.0.3. No package installation was needed. M1 source,
benchmarks, original RTL, library files, tool configuration and verification
conditions were not changed. The only existing document edited was the module
README to link these additions.

## Tests

Command:

```bash
python3 -B -m unittest discover -s rtl_agent/tests -v
```

All 35 tests passed: 14 unchanged M1 regressions and 21 new strategy tests.
New tests cover malformed YAML, missing fields, invalid types, duplicate keys
and IDs, unsafe YAML tags, capability restrictions, false sharing hints,
extra consumers, identical expressions, incompatible widths, source identity,
timing-pressure ranking, repeatability across fresh analysis runs, errors and
unsupported state/X behavior. The report-priority test uses modified metric
objects as a unit-test scenario, not a measured area or timing result.

## Real input demonstrations

The independent CLI analyzes and synthesizes the unchanged inputs. It does not
generate optimized RTL or perform a model-based A/B experiment.

```bash
python3 -B -m rtl_agent.strategies case/rtl/opt_top.v --top opt_top --depth-limit 19
python3 -B -m rtl_agent.strategies case/bench/popcount3/popcount3_v1_behavioral.v --top TopModule
```

### opt_top

Artifacts: `work/rtl_agent/strategy-demo-a7mzow5r/summary.json`.

- Generic structure: two `$mul`, one `$mux`.
- Shared-operator hint: two compatible 8x8 multiply outputs feed the two sides
  of a 16-bit mux. No extra consumers were observed. Structural confidence is
  high; no area benefit is established.
- Actual M1 baseline: area 764.218, logic depth 19, mapped cells 680.
- With the demonstration limit of 19 logic levels, admitted priorities are mux
  optimization 75, resource sharing 73, arithmetic restructuring 70.
- No matching width/Boolean evidence. FSM is disabled and lacks backend support.

Without depth pressure, the same report gives sharing 93, mux optimization 45,
arithmetic restructuring 40. These are rule priorities, not predicted savings.

### popcount3

Artifacts: `work/rtl_agent/strategy-demo-m8s6sgyu/summary.json`.

- Generic structure: two `$add`, with a direct arithmetic dependency.
- Actual M1 baseline: area 6.384, logic depth 2.
- Arithmetic restructuring is admitted with medium structural confidence and
  priority 45. Resource sharing is not admitted: two operators alone do not
  establish the required selected-output structure.

## Limits and next stage

No optimized area, equivalence pass rate, token saving or strategy effectiveness
is claimed. All strategy entries remain `reference_guidance_not_locally_measured`.
Logic depth is not nanoseconds. FSM remains disabled. Direct connectivity rules
are conservative and can miss sliced or nested opportunities. Priorities are
fixed heuristics, not a learned or official scoring function.

The next stage can call the documented APIs from analysis and selection nodes,
then ask a model to assess applicability. Later acceptance must still require
formal PASS against the original, comparable complete synthesis results and
fixed timing compliance before area comparison or best-version promotion.
