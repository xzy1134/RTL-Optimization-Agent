# RTL optimization strategy library

This is the second development stage. It loads strategy data, inspects unchanged
RTL, reads M1 synthesis artifacts, and ranks search suggestions. It does not
generate candidates, call a model, or implement an Agent loop.

## Layout

```text
rtl_agent/
  strategies/
    resource_sharing.yaml
    width_reduction.yaml
    mux_optimization.yaml
    boolean_simplification.yaml
    arithmetic_restructuring.yaml
    fsm_optimization.yaml
    schema.py
    loader.py
    selector.py
    __init__.py
    __main__.py
    README.md
  analysis/
    rtl_features.py
    yosys_report.py
    __init__.py
  eda/analysis.py
  requirements-strategies.txt
  tests/test_strategies.py
```

## Dependencies and commands

The new library uses PyYAML 6.x. It was already installed as 6.0.3 on the development
host. M1 remains standard-library-only. The separate requirements file declares
the optional dependency; no unrelated projects or tools are installed.

From the repository root:

```bash
python3 -B -m rtl_agent.strategies case/rtl/opt_top.v --top opt_top --depth-limit 19
python3 -B -m unittest discover -s rtl_agent/tests -v
```

The first command analyzes the RTL and synthesizes the unchanged baseline once.
It saves `summary.json`, generic netlist, source snapshots, M1 artifacts, and logs
under a unique `work/rtl_agent/strategy-demo-*` directory. It never writes the input.
`--depth-limit 19` is an explicit demonstration constraint in logic levels, not ns
or a confirmed course timing rule. Omit it to rank without depth pressure.

To reuse an existing synthesis run, pass `--synthesis-run <M1-run-directory>`.
The report must match the analyzed top and all source hashes. `--source` accepts
repeatable auxiliary sources. `--tools-env`, `--timeout`, and `--outdir` control
analysis and baseline execution. Source limitations are inherited from M1:
default parameters only, no includes, ordinary top identifier.

## Python interface

```python
from dataclasses import asdict
from rtl_agent.analysis import analyze_rtl, analyze_yosys_report
from rtl_agent.strategies import load_strategies, select_candidate_strategies

strategies = load_strategies()
features = analyze_rtl("case/rtl/opt_top.v", "opt_top")
report = analyze_yosys_report("work/rtl_agent/<existing-synthesis-run>")
decisions = select_candidate_strategies(
    strategies, features, report, depth_limit=19,
)
eligible = [asdict(d) for d in decisions if d.allow_llm]
```

`analyze_rtl` returns `RTLFeatures` with status, source hashes, cells, ports,
proof limitations and evidence. Status is `OK`, `ERROR` or `TIMEOUT`.
`analyze_yosys_report` reads existing artifacts and returns `OK`, `ERROR` or
`UNAVAILABLE`. It never runs or changes synthesis. Callers may omit the report
for structure-only selection. A supplied successful report with mismatched
source hashes or top blocks all LLM admission. A failed/missing report supplies
no ranking bonuses and is explicitly reported in the selection reasons.

Report reading checks metric types, finite nonnegative area, integer depth,
metric identity, and agreement between saved configuration fingerprints. It is
an artifact reader, not a tamper-proof judge. Only trusted M1 outputs should be
used. It does not convert logic depth to time.

## Strategy schema and sources

Each YAML mapping contains the requested `id`, `name`, `goal`, `applicable_when`,
`detection_hints`, `transformation`, `risks`, `validation`, and `source`. It also
contains `schema_version`, strategy `version`, `enabled`, `requires`,
`forbidden_changes`, and `evidence_level`. Sources include URL, section, and
the specific claim supported. All six entries are reference guidance, not
locally measured area improvements.

The loader rejects malformed YAML, duplicate keys, duplicate IDs, missing or
unknown fields, invalid types, and unsafe Python YAML tags. It uses a strict
subclass of `SafeLoader`. YAML cannot define executable rules. A new strategy
needs a schema-valid file and a Python rule; unknown rule IDs fail closed.

Hardware strategy guidance comes from the local `rtl-area-timing` Skill and
its upstream [source](https://github.com/LilithSemi/claude-for-hardware/blob/master/skills/rtl-area-timing/SKILL.md).
Width, sign, priority, and X rules come from
[Synthesizable RTL Style](https://github.com/zhangyi-zen/synthesizable-rtl-style/blob/main/skills/synthesizable-rtl-style/SKILL.md).
Its naming/reset preferences do not authorize changing an existing interface or
behavior. FPGA measurements are not Nangate45 evidence. Pipeline insertion,
serialization, reduced precision, changed parameter defaults, and weakened
verification assumptions are forbidden.

The library separates three concerns:

- Area strategies are hypotheses about hardware structure.
- Coding rules protect semantics and synthesizability; they do not predict area.
- Search methods from [RTLScout](https://github.com/huawei-csl/RTLScout) and
  [Dr.RTL](https://github.com/hkust-zhiyao/Dr_RTL) belong to the later controller.
  [RTL-OPT](https://github.com/hkust-zhiyao/RTL-OPT) is a future evaluation source,
  not evidence that these six proposals improve this project's area.

## Structural evidence

Analysis uses a separate fixed recipe: `read_verilog -sv`, `hierarchy -check`,
`proc`, `flatten -noscopeinfo`, `check -assert`, `write_json`. It reuses the M1
runner, timeout handling, configuration, and snapshots. It does not change M1
scoring or proof scripts. No source regex is used to infer operator structure.

The analyzer records direct connectivity and generic cell parameters:

- Sharing needs distinct, same-type arithmetic cells with matching width/sign
  parameters whose complete outputs drive opposite inputs of one `$mux`.
  Extra consumers or identical operands lower confidence. Two arithmetic cells
  without the mux connection do not satisfy this rule.
- Width hints include unused high arithmetic output bits or possible operand
  extensions. Neither proves that an operand can safely be narrowed.
- Arithmetic hints include direct operator chains, constant multiply, or weak
  operator presence. Mux, Boolean, and variable-shift presence are weak hints.
- State cells are weak state-logic hints, not an assertion that an FSM exists.

Only direct matching is implemented. Sliced, extended, nested or reordered
connections can cause conservative false negatives. There is no range analysis,
reachability proof, critical-path diagnosis, or area attribution to RTL cones.
The mapped cell histogram does not reconstruct generic operators or provide
their individual area contributions.

Evidence cell identifiers normalize snapshot paths to `source/<snapshot-name>`
for reproducible results across fresh runs. The unmodified raw netlist and source
manifest remain in the analysis directory for traceability. Confidence describes
the observed structure, never confidence in an area saving.

## Selection and admission

Every loaded strategy returns a `Recommendation`, including rejected entries:

- `id`, name, integer priority, and structural `recommended` flag.
- Reasons, evidence cells/details, confidence, and unconfirmed conditions.
- `backend_satisfied`, missing capabilities, policy blockers, and `allow_llm`.

`recommended` means a matching hint exists. Only `allow_llm` authorizes presenting
it for a future model decision. Neither authorizes accepting an optimized design.
FSM remains disabled in YAML even if a caller claims extra capabilities. All
strategies on sequential designs are blocked with current default capabilities.
Unknown cells, explicit X/Z, bidirectional ports, and analysis failures block
admission under the current proof contract.

Deterministic priorities are search heuristics, not the official scoring formula:

| Strategy | Base priority |
|---|---:|
| Resource sharing | 80 |
| Width reduction | 60 |
| Mux optimization | 45 |
| Arithmetic restructuring | 40 |
| Boolean simplification | 30 |
| FSM optimization | 20 |

High/medium structural confidence adds 10/5. A matched report with at least 64
mapped cells adds 3 to sharing/width exploration. This count is only a small
priority heuristic. If depth reaches/exceeds the configured limit, mux,
arithmetic, and Boolean exploration gain 30; sharing loses 20 due to input-mux
risk. No critical path is inferred. Entries sort by admission, descending score,
then ID. No time, random state, input order, or persisted history affects sorting.

## Later LangGraph integration

A future analysis node calls these APIs and saves feature/report references in
State. A selection node passes admitted strategy descriptions, evidence,
unconfirmed conditions, the parent RTL, and a short feedback summary to the LLM.
The LLM can reject applicability. Candidate generation is not implemented here.

The later controller must still require original-vs-candidate formal `PASS`,
successful same-configuration synthesis, and timing compliance before comparing
area or updating best. It must record strategy/version, actual diff, proof,
metrics, costs, and rejection reasons. Current priorities do not bypass any gate.
