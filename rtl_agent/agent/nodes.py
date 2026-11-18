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
from rtl_agent.llm.client import ModelRequest, ModelError, empty_usage, is_strategy_label
from rtl_agent.llm.prompt_builder import build_messages
from rtl_agent.report.storage import atomic_json, write_once
from rtl_agent.strategies import load_strategies, select_candidate_strategies
from rtl_agent.analysis.diagnostics import diagnose
from .evaluator import assess
from .memory import summarize_trial, build_search_memory, RECENT_LIMIT


def remaining(state):
    return state['deadline'] - time.perf_counter()


def config_for(state):
    return replace(EDAConfig(**state['eda_config']),
                   timeout_seconds=max(1e-6, min(state['eda_config']['timeout_seconds'], remaining(state))))


def source_key(path, extras):
    return hashlib.sha256(json.dumps([digest(path), *(digest(p) for p in extras)]).encode()).hexdigest()


def initialize(original, top, config, budget, outdir, depth_limit, extras, model_info=None, diagnostics=False):
    started = time.perf_counter()
    validate(top, config)
    for name in ('max_candidates', 'max_rounds', 'patience', 'max_api_requests', 'max_tokens'):
        if type(getattr(budget, name)) is not int or getattr(budget, name) < 1:
            raise ValueError(f'{name} must be a positive integer')
    if not math.isfinite(budget.runtime_seconds) or budget.runtime_seconds <= 0:
        raise ValueError('runtime_seconds must be positive and finite')
    if depth_limit is not None and (type(depth_limit) is not int or depth_limit < 0):
        raise ValueError('depth_limit must be a nonnegative integer or None')
    model_info = model_info or {'mode': 'mock'}
    run = new_run(outdir, 'mock-search' if model_info['mode'] == 'mock' else 'deepseek-search')
    sources = snapshot(original, extras, run, 'original')
    # Freeze library once for the entire search. M1 still uses its unchanged recipe.
    library = run / 'fixed_library.lib'
    library.write_bytes(Path(config.liberty_file).read_bytes())
    config = replace(config, liberty_file=str(library))
    write_once(run / 'experiment.json', {'top': top, 'eda_config': asdict(config),
               'budget': asdict(budget), 'depth_limit': depth_limit, 'library_sha256': digest(library),
               'model': model_info, 'diagnostics': diagnostics})
    return {'diagnostics': diagnostics, 'diagnostic_seconds': 0., 'run_dir': str(run), 'original_path': str(sources[0]), 'original_hash': digest(sources[0]),
            'extra_sources': [str(p) for p in sources[1:]], 'top': top, 'eda_config': asdict(config),
            'budget': asdict(budget), 'depth_limit': depth_limit, 'started': started,
            'deadline': started + budget.runtime_seconds, 'round_index': 0, 'candidate_count': 0,
            'passed_count': 0, 'no_improvement_rounds': 0, 'best': None, 'baseline': {},
            'baseline_feasible': False, 'config_fingerprint': None, 'baseline_proof': {},
            'attempted_hashes': {source_key(sources[0], sources[1:])}, 'failures': [], 'recent_trials': [], 'history': [],
            'recommendations': [], 'strategy_attempts': {}, 'current': {}, 'stop_reason': None,
            'model_usage': empty_usage(model_info['mode']), 'model_info': model_info,
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
        elif s['model_usage']['real_api_calls'] >= s['budget']['max_api_requests']:
            reason = 'API_REQUEST_BUDGET'
        elif s['model_usage']['charged_tokens'] >= s['budget']['max_tokens']:
            reason = 'TOKEN_BUDGET'
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
    recent = s['recent_trials'][-2:]
    locations = [r.get('diagnosis', {}).get('location') for r in recent]
    switch = s['diagnostics'] and len(locations) == 2 and bool(locations[0]) and locations[0] == locations[1]
    if switch:
        parent = s['best'] or s['baseline']
    features = analyze_rtl(parent['rtl_path'], s['top'], config=config_for(s),
                           outdir=s['run_dir'], extra_sources=s['extra_sources'])
    report = analyze_yosys_report(parent['run_dir'])
    strategies = load_strategies()
    decisions = select_candidate_strategies(strategies, features, report, depth_limit=s['depth_limit'])
    by_id = {v.id: asdict(v) for v in strategies}
    admitted = [{**asdict(d), 'strategy': by_id[d.id]} for d in decisions if d.allow_llm]
    # Least-tried strategy first, then M2's deterministic priority order.
    admitted.sort(key=lambda d: (s['strategy_attempts'].get(d['id'], 0), -d['priority'], d['id']))
    if switch:
        previous = s['current']['requested_strategy_id']
        admitted.sort(key=lambda d: d['id'] == previous)
    stop = None
    if remaining(s) <= 0:
        stop = 'RUNTIME_LIMIT'
    elif features.status != 'OK' or report.status != 'OK':
        stop = 'PARENT_ANALYSIS_ERROR'
    return {'switch_location': switch, 'parent': parent, 'recommendations': admitted,
            'primary_strategy': admitted[0]['id'] if admitted else 'open_exploration',
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
        feedback_mode='diagnostic' if s['diagnostics'] else 'historical',
        feedback=s['recent_trials'][-RECENT_LIMIT:], memory=build_search_memory(s),
        constraints={'depth_limit': s['depth_limit'], 'timing_metric': 'logic_depth',
        'exploration_mode': 'open' if s['current'].get('action') == 'NO_CHANGE' or not s['recommendations'] else 'guided',
        'formal_requirement': 'PASS against immutable original RTL',
        'api_budget': {'remaining_requests': s['budget']['max_api_requests'] - s['model_usage']['real_api_calls'],
                       'remaining_tokens': s['budget']['max_tokens'] - s['model_usage']['charged_tokens']},
        'forbidden': ['change interface/parameter defaults/precision/cycles/reset',
                      'modify tools, benchmark, library, constraints or verification assumptions']})
    if s['diagnostics']:
        previous = s['current']
        request.memory['objective'] = {
            'problem': previous.get('reason', 'Find a smaller feasible design'),
            'inspect': 'Explore another cone from this parent' if s.get('switch_location') else
                      'Use the latest diagnosis and actual edit in feedback when available',
            'avoid_repeat': previous.get('candidate_id'), 'constraints_ref': 'constraints'}
        if not s.get('switch_location') and previous.get('reason') in ('FORMAL_FAIL', 'DEPTH_LIMIT'):
            # Supply the changed lines for a repair without promoting a failed design.
            request.memory['objective']['inspect'] = 'Repair the last actual_edit using its diagnosis. Reapply to the current parent only if compatible.'
    write_once(directory / 'request.json', asdict(request))
    write_once(directory / 'messages.json', build_messages(request))
    current = {'candidate_id': f'candidate-{round_index:04d}', 'iteration': round_index,
               'parent_id': s['parent']['id'], 'strategy_id': s['primary_strategy'],
               'requested_strategy_id': s['primary_strategy'], 'directory': str(directory),
               'action': 'ERROR', 'accepted': False, 'eligible': False,
               'equivalence': {'status': 'NOT_RUN'}, 'synthesis': {}, 'started': started,
               'reason': 'MODEL_ERROR'}
    usage = dict(s['model_usage'])
    usage['calls'] += 1
    call_usage = None
    stop_reason = s['stop_reason']

    def archive_api(artifacts):
        for name in ('api_request', 'api_attempts'):
            if name in artifacts:
                write_once(directory / (name + '.json'), artifacts[name])

    try:
        output = model.generate(request, timeout=max(0, remaining(s)))
        call_usage = output.usage
        write_once(directory / 'response.json', asdict(output))
        archive_api(output.artifacts)
        current.update(action=output.action, strategy_id=output.strategy_id or s['primary_strategy'],
                       description=output.description, usage=output.usage)
        if output.action == 'NO_CHANGE':
            current['reason'] = 'NO_CHANGE'
        elif (output.action != 'CANDIDATE' or not isinstance(output.rtl, str) or not output.rtl.strip()
              or not is_strategy_label(output.strategy_id)):
            current['reason'] = 'INVALID_MODEL_OUTPUT'
        else:
            path = directory / 'candidate.v'
            write_once(path, output.rtl)
            current.update(rtl_path=str(path), source_hash=digest(path),
                           source_key=source_key(path, s['extra_sources']), reason='GENERATED')
            diff = ''.join(difflib.unified_diff(request.parent_rtl.splitlines(True), output.rtl.splitlines(True),
                                              fromfile=s['parent']['id'], tofile=current['candidate_id']))
            write_once(directory / 'change.diff', diff)
            current['diff_path'] = str(directory / 'change.diff')
    except ModelError as exc:
        call_usage = exc.usage
        current.update(reason=exc.code, message=str(exc), usage=exc.usage)
        archive_api(exc.artifacts)
        write_once(directory / 'model_error.json', {'code': exc.code, 'message': str(exc), 'usage': exc.usage})
        if exc.code in ('API_REQUEST_BUDGET', 'TOKEN_BUDGET', 'USAGE_MISSING', 'HTTP_401', 'HTTP_402',
                        'HTTP_403', 'NETWORK_ERROR', 'MODEL_TIMEOUT'):
            stop_reason = exc.code
    except (TimeoutError, ValueError, TypeError) as exc:
        current['reason'] = 'MODEL_TIMEOUT' if isinstance(exc, TimeoutError) else 'MODEL_ERROR'
        current['message'] = str(exc)[:1000]
        usage['unknown_usage_calls'] += 1
    if call_usage:
        for key in ('input_tokens', 'output_tokens', 'total_tokens', 'charged_tokens', 'real_api_calls',
                    'retries', 'unknown_usage_calls', 'api_seconds'):
            usage[key] += call_usage.get(key, 0)
    attempts = dict(s['strategy_attempts'])
    used = current['strategy_id'] if current['strategy_id'] in {r['id'] for r in s['recommendations']} else s['primary_strategy']
    attempts[used] = attempts.get(used, 0) + 1
    return {'round_index': round_index, 'candidate_count': s['candidate_count'] + int(current['action'] == 'CANDIDATE'),
            'current': current, 'model_usage': usage, 'strategy_attempts': attempts, 'stop_reason': stop_reason,
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
    summary = summarize_trial(c)
    seconds = s.get('diagnostic_seconds', 0.)
    if s.get('diagnostics') and remaining(s) > 0:
        started = time.perf_counter()
        diagnosis = diagnose(c, s)
        if diagnosis:
            write_once(Path(c['directory']) / 'diagnosis.json', diagnosis)
            summary['diagnosis'] = diagnosis
        seconds += time.perf_counter() - started
    c['trial_summary'] = summary
    best = s['best']
    if c['accepted']:
        best = {**c['synthesis'], 'id': c['candidate_id'], 'rtl_path': c['rtl_path'],
                'equivalence': 'PASS', 'proof_run': c['equivalence']['run_dir'],
                'strategy_id': c['strategy_id'], 'actual_edit': summary['actual_edit']}
    filename = str(Path(c['directory']) / 'record.json')
    write_once(filename, c)
    # Publish best only after its immutable record is safely stored.
    if c['accepted']:
        atomic_json(Path(s['run_dir']) / 'best.json', best)
    failures = s['failures']
    if not c['accepted']:
        failures = (failures + [summary])[-RECENT_LIMIT:]
    return {'diagnostic_seconds': seconds, 'current': c, 'best': best, 'history': s['history'] + [filename], 'failures': failures,
            'recent_trials': (s['recent_trials'] + [summary])[-RECENT_LIMIT:],
            'no_improvement_rounds': 0 if c['accepted'] else s['no_improvement_rounds'] + 1}
