from pathlib import Path
import time

from .storage import atomic_json


def final_report(state):
    run = Path(state['run_dir'])
    best, baseline = state.get('best'), state.get('baseline', {})
    area = baseline.get('area')
    improvement = ((area-best['area'])/area*100 if best and area else None)
    result = {
        'run_dir': str(run), 'timing_metric': 'logic_depth',
        'depth_limit': state['depth_limit'], 'depth_constraint_checked': state['depth_limit'] is not None,
        'baseline': baseline, 'baseline_feasible': state.get('baseline_feasible', False),
        'baseline_equivalence': state.get('baseline_proof'), 'best': best,
        'output_rtl': best['rtl_path'] if best else state['original_path'],
        'outcome': ('IMPROVED' if best and best['id'] != 'baseline' and improvement is not None and improvement > 0
                    else 'FEASIBLE_DESIGN' if best and best['id'] != 'baseline'
                    else 'BASELINE_RETAINED' if best else 'NO_FEASIBLE_DESIGN'),
        'area_improvement_percent': improvement, 'candidate_count': state['candidate_count'],
        'passed_count': state['passed_count'], 'rounds': state['round_index'],
        'stop_reason': state['stop_reason'], 'model_usage': state['model_usage'],
        'evaluation_seconds': state['evaluation_seconds'], 'model_seconds': state['model_seconds'],
        'history': state['history'], 'config_fingerprint': state.get('config_fingerprint'),
    }
    lines = ['# Mock RTL search report', '', f"Outcome: {result['outcome']}",
             f"Stop: {result['stop_reason']}", f"Output RTL: {result['output_rtl']}",
             'Timing metric: logic_depth (logic levels, not ns).',
             f"Depth limit: {state['depth_limit'] if state['depth_limit'] is not None else 'NOT SET; no upper-bound constraint checked'}",
             f"Baseline feasible: {result['baseline_feasible']}",
             f"Baseline area/depth: {baseline.get('area')} / {baseline.get('logic_depth')}",
             f"Best area/depth: {best['area'] if best else None} / {best['logic_depth'] if best else None}",
             f"Area improvement (%): {improvement}",
             f"Candidates / formal PASS: {state['candidate_count']} / {state['passed_count']}",
             'Model: Mock; real API calls and real tokens: 0.', '',
             '| Candidate | Strategy | Equivalence | Area | Depth | Accepted | Reason |',
             '|---|---|---|---:|---:|---|---|']
    import json
    for filename in state['history']:
        record = json.loads(Path(filename).read_text())
        if record.get('action') != 'CANDIDATE':
            continue
        syn = record.get('synthesis', {})
        lines.append(f"| {record['candidate_id']} | {record['strategy_id']} | {record.get('equivalence', {}).get('status', 'NOT_RUN')} | {syn.get('area')} | {syn.get('logic_depth')} | {record['accepted']} | {record['reason']} |")
    path = run / 'report.md'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    # Measure through report generation; final JSON serialization adds small overhead.
    result['runtime_seconds'] = time.perf_counter() - state['started']
    with path.open('a', encoding='utf-8') as out:
        out.write(f"\nRuntime (s): {result['runtime_seconds']:.6f}\n")
    atomic_json(run / 'report.json', result)
    return {'report_path': str(run / 'report.json')}
