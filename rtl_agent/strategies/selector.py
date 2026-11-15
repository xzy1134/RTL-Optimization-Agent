"""Deterministic hint ranking. Scores express search priority, never PPA benefit."""
from dataclasses import dataclass, field

DEFAULT_CAPABILITIES = frozenset({'combinational_equivalence', 'mapped_area', 'logic_depth'})
RULES = {
    'resource_sharing': ({'shared_operator_mux'}, 80),
    'width_reduction': ({'unused_high_output_bits', 'operand_extension_hint'}, 60),
    'mux_optimization': ({'mux_presence', 'variable_shift'}, 45),
    'boolean_simplification': ({'boolean_presence'}, 30),
    'arithmetic_restructuring': ({'arithmetic_chain', 'constant_multiply', 'arithmetic_presence'}, 40),
    'fsm_optimization': ({'state_logic_hint'}, 20),
}


@dataclass
class Recommendation:
    id: str
    name: str
    priority: int
    recommended: bool
    reasons: list[str]
    evidence: list[dict]
    confidence: str
    unconfirmed_conditions: list[str]
    backend_satisfied: bool
    missing_capabilities: list[str]
    allow_llm: bool
    blockers: list[str] = field(default_factory=list)


def select_candidate_strategies(strategies, features, report=None, *, capabilities=DEFAULT_CAPABILITIES,
                                depth_limit=None):
    if depth_limit is not None and (type(depth_limit) is not int or depth_limit < 0):
        raise ValueError('depth_limit must be a nonnegative integer, measured in logic levels')
    from dataclasses import asdict
    results = []
    valid_report = report is not None and report.status == 'OK'
    report_matches = valid_report and report.top == features.top and report.source_hashes == features.source_hashes
    for strategy in sorted(strategies, key=lambda s: s.id):
        kinds, base = RULES.get(strategy.id, (set(), 0))
        evidence = [e for e in features.evidence if e.kind in kinds]
        confidence = max((e.confidence for e in evidence), key={'low': 0, 'medium': 1, 'high': 2}.get, default='none')
        missing = sorted(set(strategy.requires) - set(capabilities))
        if features.sequential_cells and 'sequential_equivalence' not in capabilities:
            missing = sorted(set(missing) | {'sequential_equivalence'})
        if features.sequential_cells:
            missing = sorted(set(missing) | ({'sequential_equivalence', 'sequential_mapped_area', 'constrained_timing'} - set(capabilities)))
        blockers = []
        if not strategy.enabled:
            blockers.append('Strategy disabled by library policy')
        if features.status != 'OK':
            blockers.append('RTL analysis did not succeed')
        blockers.extend(features.proof_limitations)
        if features.unsupported_cells and not (features.sequential_cells and 'sequential_equivalence' in capabilities
                                               and set(features.unsupported_cells) <= set(features.sequential_cells)):
            blockers.append('Netlist contains cells unsupported by the current proof contract')
        if any(p['direction'] == 'inout' for p in features.ports.values()):
            blockers.append('Bidirectional ports are outside the current proof contract')
        if not evidence:
            blockers.append('No matching structural evidence')
        if strategy.id not in RULES:
            blockers.append('No implemented deterministic rule for this strategy')
        if missing:
            blockers.append('Missing backend capabilities: ' + ', '.join(missing))
        if valid_report and not report_matches:
            blockers.append('Synthesis report does not match analyzed source hashes/top')
        reasons = [f'{e.kind}: {", ".join(e.cells)}' for e in evidence]
        score = base + (10 if confidence == 'high' else 5 if confidence == 'medium' else 0) if evidence else 0
        if evidence and report_matches:
            if report.mapped_cell_count >= 64 and strategy.id in ('resource_sharing', 'width_reduction'):
                score += 3
                reasons.append('At least 64 mapped cells: small search-priority bonus, not an area-saving prediction')
            if depth_limit is not None and report.logic_depth >= depth_limit and strategy.id in ('mux_optimization', 'arithmetic_restructuring', 'boolean_simplification'):
                score += 30
                reasons.append('Baseline reaches/exceeds configured logic-depth limit; explore path structure first')
            if depth_limit is not None and report.logic_depth >= depth_limit and strategy.id == 'resource_sharing':
                score -= 20
                reasons.append('Input MUX may worsen a tight logic-depth budget')
        if report is not None and report.status != 'OK':
            reasons.append('Synthesis summary unavailable; ranking uses generic structure only')
        unconfirmed = sorted({condition for e in evidence for condition in e.unconfirmed} |
                             set(strategy.applicable_when) | {'LLM must assess semantic applicability; prove equivalence and measure PPA'})
        results.append(Recommendation(strategy.id, strategy.name, score, bool(evidence), reasons,
                       [asdict(e) for e in evidence], confidence, unconfirmed, not missing, missing,
                       not blockers, blockers))
    return sorted(results, key=lambda r: (not r.allow_llm, -r.priority, r.id))
