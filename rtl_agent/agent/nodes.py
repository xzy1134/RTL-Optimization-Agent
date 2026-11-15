"""Graph nodes. Each returns partial updates; all EDA decisions are tool-derived."""
from dataclasses import asdict, replace
import difflib
import hashlib
import json
import math
from pathlib import Path
import time

from rtl_agent.analysis import analyze_rtl, analyze_yosys_report
from rtl_agent.eda import EDAConfig, synthesize_rtl, verify_rtl
from rtl_agent.eda.common import digest, new_run, snapshot, validate
from rtl_agent.eda.verifier import interface
from rtl_agent.llm.client import ModelRequest
from rtl_agent.report.storage import atomic_json, write_once
from rtl_agent.strategies import load_strategies, select_candidate_strategies
from .evaluator import assess


def remaining(state):
    return state['deadline'] - time.perf_counter()


def config_for(state):
    return replace(EDAConfig(**state['eda_config']),
                   timeout_seconds=max(1e-6, min(state['eda_config']['timeout_seconds'], remaining(state))))


def source_key(path, extras):
    return hashlib.sha256(json.dumps([digest(path), *(digest(p) for p in extras)]).encode()).hexdigest()


def initialize(original, top, config, budget, outdir, depth_limit, extras):
    started = time.perf_counter()
    validate(top, config)
    for name in ('max_candidates', 'max_rounds', 'patience'):
        if type(getattr(budget, name)) is not int or getattr(budget, name) < 1:
            raise ValueError(f'{name} must be a positive integer')
    if not math.isfinite(budget.runtime_seconds) or budget.runtime_seconds <= 0:
        raise ValueError('runtime_seconds must be positive and finite')
    if depth_limit is not None and (type(depth_limit) is not int or depth_limit < 0):
        raise ValueError('depth_limit must be a nonnegative integer or None')
    run = new_run(outdir, 'mock-search')
    sources = snapshot(original, extras, run, 'original')
    # Freeze library once for the entire search. M1 still uses its unchanged recipe.
    library = run / 'fixed_library.lib'
    library.write_bytes(Path(config.liberty_file).read_bytes())
    config = replace(config, liberty_file=str(library))
    write_once(run / 'experiment.json', {'top': top, 'eda_config': asdict(config),
               'budget': asdict(budget), 'depth_limit': depth_limit, 'library_sha256': digest(library)})
    return {'run_dir': str(run), 'original_path': str(sources[0]), 'original_hash': digest(sources[0]),
            'extra_sources': [str(p) for p in sources[1:]], 'top': top, 'eda_config': asdict(config),
            'budget': asdict(budget), 'depth_limit': depth_limit, 'started': started,
            'deadline': started + budget.runtime_seconds, 'round_index': 0, 'candidate_count': 0,
            'passed_count': 0, 'no_improvement_rounds': 0, 'best': None, 'baseline': {},
            'baseline_feasible': False, 'config_fingerprint': None, 'baseline_proof': {},
            'attempted_hashes': {source_key(sources[0], sources[1:])}, 'failures': [], 'history': [],
            'recommendations': [], 'strategy_attempts': {}, 'current': {}, 'stop_reason': None,
            'model_usage': {'mode': 'mock', 'input_tokens': 0, 'output_tokens': 0,
                            'real_api_calls': 0, 'calls': 0, 'unknown_usage_calls': 0},
            'evaluation_seconds': 0.0, 'model_seconds': 0.0}


def baseline_node(s):
    if remaining(s) <= 0:
        return {'stop_reason': 'RUNTIME_LIMIT'}
    started = time.perf_counter()
    result = asdict(synthesize_rtl(s['original_path'], s['top'], config=config_for(s),
                                  outdir=s['run_dir'], extra_sources=s['extra_sources']))
    baseline = {**result, 'id': 'baseline', 'rtl_path': s['original_path']}
    updates = {'baseline': baseline, 'config_fingerprint': result['config_fingerprint']}
    if result['status'] != 'OK':
        updates['stop_reason'] = 'RUNTIME_LIMIT' if remaining(s) <= 0 else 'BASELINE_SYNTHESIS_ERROR'
    elif remaining(s) <= 0:
        updates['stop_reason'] = 'RUNTIME_LIMIT'
    else:
        proof = asdict(verify_rtl(s['original_path'], s['original_path'], s['top'], config=config_for(s),
                       outdir=s['run_dir'], original_sources=s['extra_sources'], candidate_sources=s['extra_sources']))
        feasible = assess(proof, result, result['config_fingerprint'], s['depth_limit'], None)['eligible']
        updates.update(baseline_proof=proof, baseline_feasible=feasible)
        if proof['status'] != 'PASS':
            updates['stop_reason'] = 'RUNTIME_LIMIT' if remaining(s) <= 0 else 'BASELINE_PROOF_' + proof['status']
        if feasible:
            best = {**baseline, 'equivalence': 'PASS', 'proof_run': proof['run_dir']}
            atomic_json(Path(s['run_dir']) / 'best.json', best)
            updates['best'] = best
    updates['evaluation_seconds'] = time.perf_counter() - started
    return updates


def control_node(s):
    reason = s.get('stop_reason')
    if not reason:
        if remaining(s) <= 0:
            reason = 'RUNTIME_LIMIT'
        elif s['candidate_count'] >= s['budget']['max_candidates']:
            reason = 'MAX_CANDIDATES'
        elif s['round_index'] >= s['budget']['max_rounds']:
            reason = 'MAX_ROUNDS'
        elif s['no_improvement_rounds'] >= s['budget']['patience']:
            reason = 'NO_IMPROVEMENT'
    return {'stop_reason': reason}


def select_node(s):
    started = time.perf_counter()
    # A failed attempt never becomes a parent. Every third attempt can restart
    # from the immutable original to avoid a single search trajectory.
    parent = s['best'] if s['best'] and s['round_index'] % 3 != 2 else s['baseline']
    features = analyze_rtl(parent['rtl_path'], s['top'], config=config_for(s),
                           outdir=s['run_dir'], extra_sources=s['extra_sources'])
    report = analyze_yosys_report(parent['run_dir'])
    strategies = load_strategies()
    decisions = select_candidate_strategies(strategies, features, report, depth_limit=s['depth_limit'])
    by_id = {v.id: asdict(v) for v in strategies}
    admitted = [{**asdict(d), 'strategy': by_id[d.id]} for d in decisions if d.allow_llm]
    # Least-tried strategy first, then M2's deterministic priority order.
    admitted.sort(key=lambda d: (s['strategy_attempts'].get(d['id'], 0), -d['priority'], d['id']))
    stop = None
    if remaining(s) <= 0:
        stop = 'RUNTIME_LIMIT'
    elif features.status != 'OK' or report.status != 'OK':
        stop = 'PARENT_ANALYSIS_ERROR'
    elif not admitted:
        stop = 'NO_AVAILABLE_STRATEGIES'
    return {'parent': parent, 'recommendations': admitted, 'primary_strategy': admitted[0]['id'] if admitted else '',
            'stop_reason': stop, 'evaluation_seconds': s['evaluation_seconds'] + time.perf_counter() - started}


def generate_node(s, model):
    started = time.perf_counter()
    round_index = s['round_index'] + 1
    directory = Path(s['run_dir']) / f'round-{round_index:04d}'
    directory.mkdir()  # Unique immutable namespace, never overwrite prior candidates.
    request = ModelRequest(
        original_rtl=Path(s['original_path']).read_text(), parent_rtl=Path(s['parent']['rtl_path']).read_text(),
        top=s['top'], primary_strategy=s['primary_strategy'], strategies=s['recommendations'],
        metrics={'baseline_area': s['baseline']['area'], 'baseline_depth': s['baseline']['logic_depth'],
                 'parent_area': s['parent']['area'], 'parent_depth': s['parent']['logic_depth']},
        feedback=s['failures'][-5:], constraints={'depth_limit': s['depth_limit'], 'timing_metric': 'logic_depth',
        'formal_requirement': 'PASS against immutable original RTL',
        'forbidden': ['change interface/parameter defaults/precision/cycles/reset',
                      'modify tools, benchmark, library, constraints or verification assumptions']})
    write_once(directory / 'request.json', asdict(request))
    current = {'candidate_id': f'candidate-{round_index:04d}', 'iteration': round_index,
               'parent_id': s['parent']['id'], 'strategy_id': s['primary_strategy'],
               'requested_strategy_id': s['primary_strategy'], 'directory': str(directory),
               'action': 'ERROR', 'accepted': False, 'eligible': False,
               'equivalence': {'status': 'NOT_RUN'}, 'synthesis': {}, 'started': started,
               'reason': 'MODEL_ERROR'}
    usage = dict(s['model_usage'])
    usage['calls'] += 1
    try:
        output = model.generate(request, timeout=max(0, remaining(s)))
        write_once(directory / 'response.json', asdict(output))
        current.update(action=output.action, strategy_id=output.strategy_id or s['primary_strategy'],
                       description=output.description, usage=output.usage)
        if output.action == 'NO_CHANGE':
            current['reason'] = 'NO_CHANGE'
        elif output.action != 'CANDIDATE' or not isinstance(output.rtl, str) or not output.rtl.strip():
            current['reason'] = 'INVALID_MODEL_OUTPUT'
        elif output.strategy_id not in {r['id'] for r in s['recommendations']}:
            current['reason'] = 'STRATEGY_NOT_ADMITTED'
        else:
            path = directory / 'candidate.v'
            write_once(path, output.rtl)
            current.update(rtl_path=str(path), source_hash=digest(path),
                           source_key=source_key(path, s['extra_sources']), reason='GENERATED')
            diff = ''.join(difflib.unified_diff(request.parent_rtl.splitlines(True), output.rtl.splitlines(True),
                                              fromfile=s['parent']['id'], tofile=current['candidate_id']))
            write_once(directory / 'change.diff', diff)
            current['diff_path'] = str(directory / 'change.diff')
    except (TimeoutError, ValueError, TypeError) as exc:
        current['reason'] = 'MODEL_TIMEOUT' if isinstance(exc, TimeoutError) else 'MODEL_ERROR'
        current['message'] = str(exc)[:1000]
        usage['unknown_usage_calls'] += 1
    # Mock calls have explicitly simulated zero tokens. Real usage accumulation
    # and transport retries are M4 work, not inferred from text length here.
    attempts = dict(s['strategy_attempts'])
    used = current['strategy_id'] if current['strategy_id'] in {r['id'] for r in s['recommendations']} else s['primary_strategy']
    attempts[used] = attempts.get(used, 0) + 1
    return {'round_index': round_index, 'candidate_count': s['candidate_count'] + int(current['action'] == 'CANDIDATE'),
            'current': current, 'model_usage': usage, 'strategy_attempts': attempts,
            'model_seconds': s['model_seconds'] + time.perf_counter() - started}


def precheck_node(s):
    started = time.perf_counter()
    c = dict(s['current'])
    if remaining(s) <= 0:
        c['reason'] = 'RUNTIME_LIMIT'
        return {'current': c}
    analysis = analyze_rtl(c['rtl_path'], s['top'], config=config_for(s),
                           outdir=c['directory'], extra_sources=s['extra_sources'])
    c['precheck'] = {'status': analysis.status, 'message': analysis.message, 'run_dir': analysis.run_dir}
    if analysis.status != 'OK':
        c['reason'] = 'PRECHECK_' + analysis.status
    else:
        # Read raw JSON to compare ranges, signedness and parameter defaults with
        # exactly the M1 interface rules, in addition to the full formal check.
        data = json.loads((Path(analysis.run_dir) / 'generic.json').read_text())['modules'][s['top']]
        orig = s['baseline_proof'].get('run_dir')
        original_module = json.loads((Path(orig) / 'gold.json').read_text())['modules']['gold'] if orig else None
        if original_module is None or interface(data) != interface(original_module) or data.get('parameter_default_values', {}) != original_module.get('parameter_default_values', {}):
            c['reason'] = 'INTERFACE_MISMATCH'
        elif c['source_key'] in s['attempted_hashes']:
            c['reason'] = 'DUPLICATE'
        else:
            c['reason'] = 'PRECHECK_OK'
    return {'current': c, 'attempted_hashes': s['attempted_hashes'] | {c['source_key']},
            'evaluation_seconds': s['evaluation_seconds'] + time.perf_counter() - started}


def verify_node(s):
    started = time.perf_counter()
    c = dict(s['current'])
    if remaining(s) <= 0:
        c['reason'] = 'RUNTIME_LIMIT'
    else:
        proof = asdict(verify_rtl(s['original_path'], c['rtl_path'], s['top'], config=config_for(s),
                       outdir=c['directory'], original_sources=s['extra_sources'], candidate_sources=s['extra_sources']))
        c.update(equivalence=proof, reason='FORMAL_PASS' if proof['status'] == 'PASS' else 'FORMAL_' + proof['status'])
    return {'current': c, 'evaluation_seconds': s['evaluation_seconds'] + time.perf_counter() - started,
            'passed_count': s['passed_count'] + int(c['equivalence']['status'] == 'PASS')}


def synthesize_node(s):
    started = time.perf_counter()
    c = dict(s['current'])
    if remaining(s) <= 0:
        c['reason'] = 'RUNTIME_LIMIT'
    else:
        syn = asdict(synthesize_rtl(c['rtl_path'], s['top'], config=config_for(s),
                      outdir=c['directory'], extra_sources=s['extra_sources']))
        c['synthesis'] = syn
        c.update(assess(c['equivalence'], syn, s['config_fingerprint'], s['depth_limit'], s['best']))
        if remaining(s) <= 0:
            c.update(accepted=False, eligible=False, reason='RUNTIME_LIMIT')
    return {'current': c, 'evaluation_seconds': s['evaluation_seconds'] + time.perf_counter() - started}


def record_node(s):
    c = dict(s['current'])
    c['runtime_seconds'] = time.perf_counter() - c.pop('started')
    c.update(config_fingerprint=s['config_fingerprint'], depth_limit=s['depth_limit'], timing_metric='logic_depth')
    best = s['best']
    if c['accepted']:
        best = {**c['synthesis'], 'id': c['candidate_id'], 'rtl_path': c['rtl_path'],
                'equivalence': 'PASS', 'proof_run': c['equivalence']['run_dir']}
    filename = str(Path(c['directory']) / 'record.json')
    write_once(filename, c)
    # Publish best only after its immutable record is safely stored.
    if c['accepted']:
        atomic_json(Path(s['run_dir']) / 'best.json', best)
    failures = s['failures']
    if not c['accepted']:
        message = c.get('message') or c.get('precheck', {}).get('message') or c['equivalence'].get('message', '')
        logtail = ''
        tool_dir = c.get('equivalence', {}).get('run_dir') or c.get('precheck', {}).get('run_dir')
        if tool_dir:
            for name in ('proof.log', 'prepare.log', 'analysis.log'):
                path = Path(tool_dir) / name
                if path.exists():
                    logtail = path.read_text(errors='replace')[-1500:]
                    break
        failures = (failures + [{'candidate_id': c['candidate_id'], 'strategy_id': c['strategy_id'],
                                 'reason': c['reason'], 'message': message[:1000], 'diagnostic': logtail}])[-5:]
    stop = 'NO_CHANGE' if c['action'] == 'NO_CHANGE' else s['stop_reason']
    return {'current': c, 'best': best, 'history': s['history'] + [filename], 'failures': failures,
            'no_improvement_rounds': 0 if c['accepted'] else s['no_improvement_rounds'] + 1, 'stop_reason': stop}
