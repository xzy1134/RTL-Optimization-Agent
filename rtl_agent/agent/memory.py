"""Bounded experiment notes based on tool records, not model recollection."""
from pathlib import Path

from rtl_agent.eda.common import digest

RECENT_LIMIT = 5


def summarize_trial(record):
    synthesis = record.get('synthesis', {})
    actual_edit = ''
    if record.get('diff_path'):
        lines = Path(record['diff_path']).read_text().splitlines()
        actual_edit = '\n'.join(line for line in lines if line.startswith(('+', '-'))
                                and not line.startswith(('+++', '---')))[:600]
    diagnostic = ''
    tool_dir = record.get('precheck', {}).get('run_dir')
    if record.get('reason', '').startswith('PRECHECK_') and tool_dir:
        log = Path(tool_dir) / 'analysis.log'
        if log.exists():
            errors = [line for line in log.read_text(errors='replace').splitlines() if 'ERROR:' in line]
            diagnostic = '\n'.join(errors[-2:]).replace(tool_dir, '<tool-run>')[:300]
    return {
        'candidate_id': record['candidate_id'], 'parent_id': record['parent_id'],
        'strategy_id': record['strategy_id'], 'action': record['action'],
        'source_hash': record.get('source_hash'),
        'reported_change': record.get('description', '')[:160], 'actual_edit': actual_edit,
        'equivalence': record.get('equivalence', {}).get('status', 'NOT_RUN'),
        'synthesis': synthesis.get('status', 'NOT_RUN'),
        'candidate_area': synthesis.get('area'), 'candidate_depth': synthesis.get('logic_depth'),
        'depth_limit': record['depth_limit'], 'timing_pass': record.get('timing_pass'),
        'accepted': record['accepted'], 'reason': record['reason'],
        'message': record.get('message', '')[:240], 'diagnostic': diagnostic,
    }


def build_search_memory(state):
    def design(value):
        if not value:
            return None
        path = value['rtl_path']
        if path == state['original_path']:
            source = {'rtl_ref': 'original_rtl'}
        elif path == state['parent']['rtl_path']:
            source = {'rtl_ref': 'parent_rtl'}
        else:
            source = {'rtl': Path(path).read_text()}
        depth = value.get('logic_depth')
        return {'id': value['id'], 'source_hash': digest(path), 'area': value.get('area'),
                'logic_depth': depth,
                'equivalence': state['baseline_proof'].get('status') if value['id'] == 'baseline' else value.get('equivalence'),
                'timing_pass': type(depth) is int and (state['depth_limit'] is None or depth <= state['depth_limit']),
                'strategy_id': value.get('strategy_id'), 'actual_edit': value.get('actual_edit', ''), **source}
    return {'baseline': design(state['baseline']), 'best': design(state['best']),
            'recent_trials_ref': 'feedback',
            'older_trials_omitted': max(0, len(state['history']) - RECENT_LIMIT),
            'scope': 'This experiment only. Values are tool measurements; reported_change is model text.'}
