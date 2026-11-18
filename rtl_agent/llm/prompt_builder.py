"""Render model messages without invoking a provider or parsing its output."""
from dataclasses import asdict
import json
from pathlib import Path
from string import Template

from .client import ModelRequest

PROMPTS = Path(__file__).parent / 'prompts'


def compact_context(request: ModelRequest) -> dict:
    """Build provider context without changing the full, locally archived request.

    References only point inside this request. Evidence and semantic caveats are
    retained; library bibliography and ranking bookkeeping stay in request.json.
    """
    context = asdict(request)
    context.pop('feedback_mode')
    if request.feedback_mode == 'basic':
        context['primary_strategy'] = 'open_exploration'
        context['strategies'] = []
        context['feedback'] = [
            {key: trial[key] for key in ('equivalence', 'reason') if key in trial}
            for trial in context['feedback']]
        baseline = request.metrics.get('baseline_area')
        parent = request.metrics.get('parent_area')
        limit = request.constraints.get('depth_limit')
        if baseline is not None and parent is not None:
            depth_goal = f'depth at most {limit}' if limit is not None else 'no depth upper bound is set'
            objective = (f'Repair parent_rtl. Keep area below {baseline} and {depth_goal}; '
                         f'if possible retain area <={parent}.')
        else:
            objective = 'Optimize parent_rtl under the supplied functional and area/depth constraints.'
        context['memory'] = {'objective': objective}
        return context
    if request.feedback_mode not in ('historical', 'diagnostic'):
        raise ValueError('Unknown feedback mode')
    if request.parent_rtl == request.original_rtl:
        context.pop('parent_rtl')
        context['parent_rtl_ref'] = 'original_rtl'

    observations = []
    strategies = []
    for recommendation in context['strategies']:
        strategy = recommendation.get('strategy', {})
        refs = []
        for evidence in recommendation.get('evidence', []):
            if evidence not in observations:
                observations.append(evidence)
            refs.append(observations.index(evidence))
        hint = {'id': recommendation['id'], 'observation_refs': refs}
        for key in ('confidence', 'unconfirmed_conditions', 'allow_llm',
                    'backend_satisfied', 'missing_capabilities', 'blockers'):
            if key in recommendation:
                hint[key] = recommendation[key]
        for source, target in (('transformation', 'try'), ('risks', 'risks'),
                               ('forbidden_changes', 'forbidden_changes')):
            if source in strategy:
                hint[target] = strategy[source]
        # Unknown/ad hoc hints may lack structured evidence; retain their reasons.
        reasons = [reason for reason in recommendation.get('reasons', [])
                   if not any(reason == f"{e['kind']}: {', '.join(e['cells'])}"
                              for e in recommendation.get('evidence', []))]
        if reasons:
            hint['ranking_notes'] = reasons
        # A condition shared by several observations need only be stated once.
        conditions = list(dict.fromkeys([*hint.get('unconfirmed_conditions', []),
                                        *strategy.get('applicable_when', [])]))
        observed_conditions = {condition for index in refs
                               for condition in observations[index].get('unconfirmed', [])}
        if conditions:
            hint['unconfirmed_conditions'] = [c for c in conditions if c not in observed_conditions]
        strategies.append(hint)
    context['strategies'] = strategies
    if observations:
        context['observations'] = observations

    memory = context['memory']
    for name in ('baseline', 'best'):
        design = memory.get(name)
        if not design:
            continue
        if 'rtl' in design:
            for field in ('original_rtl', 'parent_rtl'):
                if design['rtl'] == getattr(request, field):
                    design.pop('rtl')
                    design['rtl_ref'] = 'original_rtl' if request.parent_rtl == request.original_rtl else field
                    break
        if design.get('rtl_ref') == 'parent_rtl' and 'parent_rtl_ref' in context:
            design['rtl_ref'] = context['parent_rtl_ref']
        prefix = 'baseline' if design.get('rtl_ref') == 'original_rtl' else 'parent' if design.get('rtl_ref') == 'parent_rtl' else None
        if prefix:
            for field, metric in (('area', f'{prefix}_area'), ('logic_depth', f'{prefix}_depth')):
                if field in design and metric in context['metrics'] and design[field] == context['metrics'][metric]:
                    design.pop(field)
                    design[field + '_ref'] = 'metrics.' + metric
    if memory.get('best') and memory['best'] == memory.get('baseline'):
        memory['best'] = {'design_ref': 'memory.baseline'}
    if request.parent_rtl == request.original_rtl:
        for field in ('area', 'depth'):
            parent, baseline = 'parent_' + field, 'baseline_' + field
            if parent in context['metrics'] and baseline in context['metrics'] and context['metrics'][parent] == context['metrics'][baseline]:
                context['metrics'].pop(parent)
                context['metrics'][parent + '_ref'] = 'metrics.' + baseline
    for trial in context['feedback']:
        if trial.get('depth_limit') == context['constraints'].get('depth_limit'):
            trial.pop('depth_limit', None)
        for field in ('message', 'diagnostic', 'actual_edit', 'reported_change'):
            if not trial.get(field):
                trial.pop(field, None)
    return context


def build_messages(request: ModelRequest) -> list[dict[str, str]]:
    context = json.dumps(compact_context(request), ensure_ascii=False, separators=(',', ':'))
    return [
        {'role': 'system', 'content': (PROMPTS / 'system.md').read_text(encoding='utf-8')},
        {'role': 'user', 'content': Template((PROMPTS / 'user.md').read_text(encoding='utf-8')).substitute(context=context)},
    ]
