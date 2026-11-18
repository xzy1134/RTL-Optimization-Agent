from dataclasses import asdict, replace
import json
import unittest

from rtl_agent.llm.client import ModelRequest
from rtl_agent.llm.prompt_builder import build_messages, compact_context
from rtl_agent.strategies.loader import load_strategies


class PromptTests(unittest.TestCase):
    def request(self):
        return ModelRequest(
            original_rtl='module t; // $context {{RTL}} "原始"\nendmodule',
            parent_rtl='module t; // `timescale and $display\nendmodule',
            feedback_mode='diagnostic', top='t', primary_strategy='resource_sharing',
            strategies=[{'id': 'resource_sharing', 'allow_llm': True}],
            metrics={'parent_area': 75.278, 'parent_depth': 6},
            feedback=[{'reason': 'FORMAL_FAIL'}],
            constraints={'depth_limit': None, 'timing_metric': 'logic_depth'})

    def test_preserves_request_data_and_verilog_characters(self):
        request = self.request()
        messages = build_messages(request)
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        data = json.loads(messages[1]['content'].split('\n', 1)[1])
        for key in ('original_rtl', 'parent_rtl', 'top', 'metrics', 'feedback', 'constraints'):
            self.assertEqual(data[key], asdict(request)[key])
        self.assertIn('NO_CHANGE', messages[0]['content'])

    def test_same_request_same_messages(self):
        self.assertEqual(build_messages(self.request()), build_messages(self.request()))

    def test_depth_constraint_and_feedback_in_context(self):
        messages = build_messages(self.request())
        data = json.loads(messages[1]['content'].split('\n', 1)[1])
        self.assertIsNone(data['constraints']['depth_limit'])
        self.assertEqual(data['feedback'][0]['reason'], 'FORMAL_FAIL')
        self.assertEqual(data['strategies'][0]['id'], 'resource_sharing')

    def test_strategy_compression_preserves_evidence_risks_and_conditions(self):
        strategy = asdict(next(s for s in load_strategies() if s.id == 'resource_sharing'))
        evidence = {'kind': 'shared_operator_mux', 'cells': ['mul_a', 'mul_b', 'select'],
                    'details': {'operator': '$mul', 'width': 16, 'extra_consumers': True},
                    'confidence': 'medium', 'unconfirmed': ['Check signed operands']}
        recommendation = {'id': strategy['id'], 'strategy': strategy, 'evidence': [evidence],
            'reasons': ['shared_operator_mux: mul_a, mul_b, select'],
            'confidence': 'medium', 'unconfirmed_conditions': ['Check signed operands'],
            'allow_llm': False, 'backend_satisfied': False,
            'missing_capabilities': ['mapped_area'], 'blockers': ['Missing backend']}
        request = replace(self.request(), strategies=[recommendation])
        before = asdict(request)
        context = compact_context(request)
        hint = context['strategies'][0]
        self.assertEqual(context['observations'][hint['observation_refs'][0]], evidence)
        self.assertEqual(hint['try'], strategy['transformation'])
        self.assertEqual(hint['risks'], strategy['risks'])
        self.assertEqual(hint['forbidden_changes'], strategy['forbidden_changes'])
        self.assertEqual(hint['unconfirmed_conditions'], list(strategy['applicable_when']))
        self.assertFalse(hint['allow_llm'])
        self.assertFalse(hint['backend_satisfied'])
        self.assertEqual(hint['missing_capabilities'], ['mapped_area'])
        self.assertEqual(hint['blockers'], ['Missing backend'])
        self.assertNotIn('source', hint)
        self.assertNotIn('ranking_notes', hint)
        self.assertEqual(asdict(request), before)

    def test_identical_sources_and_metrics_use_local_references(self):
        source = self.request().original_rtl
        design = {'id': 'baseline', 'area': 2.5, 'logic_depth': 3,
                  'equivalence': 'PASS', 'timing_pass': True, 'rtl_ref': 'original_rtl'}
        request = replace(self.request(), parent_rtl=source,
            metrics={'baseline_area': 2.5, 'baseline_depth': 3, 'parent_area': 2.5, 'parent_depth': 3},
            memory={'baseline': design, 'best': design})
        context = compact_context(request)
        self.assertNotIn('parent_rtl', context)
        self.assertEqual(context['parent_rtl_ref'], 'original_rtl')
        self.assertEqual(context['metrics']['parent_area_ref'], 'metrics.baseline_area')
        self.assertEqual(context['memory']['baseline']['area_ref'], 'metrics.baseline_area')
        self.assertEqual(context['memory']['best'], {'design_ref': 'memory.baseline'})
        self.assertEqual(context['memory']['baseline']['equivalence'], 'PASS')

    def test_restart_keeps_distinct_best_source_and_measurements(self):
        request = replace(self.request(), parent_rtl=self.request().original_rtl,
            memory={'best': {'id': 'candidate-1', 'rtl': 'module best; endmodule',
                            'area': 1.5, 'logic_depth': 2, 'equivalence': 'PASS'}})
        context = compact_context(request)
        self.assertEqual(context['memory']['best'], request.memory['best'])
        self.assertEqual(context['parent_rtl_ref'], 'original_rtl')

    def test_success_failure_feedback_retains_measured_values_and_actual_edit(self):
        feedback = [dict(candidate_id='c1', parent_id='baseline', accepted=True, reason='ACCEPTED',
                         candidate_area=1.5, candidate_depth=2, equivalence='PASS',
                         depth_limit=3, reported_change='share', actual_edit='-old\n+new', message=''),
                    dict(candidate_id='c2', parent_id='c1', accepted=False, reason='DEPTH_LIMIT',
                         candidate_area=1.0, candidate_depth=4, equivalence='PASS', depth_limit=3)]
        request = replace(self.request(), feedback=feedback, constraints={'depth_limit': 3})
        context = compact_context(request)
        for original, compressed in zip(feedback, context['feedback']):
            for key, value in original.items():
                if key not in ('depth_limit', 'message'):
                    self.assertEqual(compressed[key], value)
        self.assertEqual(context['constraints'], request.constraints)

    def test_observation_reused_without_losing_structural_relationships(self):
        evidence = {'kind': 'arithmetic_chain', 'cells': ['a', 'b'],
                    'details': {'operator': '$add'}, 'confidence': 'medium', 'unconfirmed': []}
        request = replace(self.request(), strategies=[{'id': label, 'evidence': [evidence]}
                          for label in ('arithmetic_restructuring', 'custom_hint')])
        context = compact_context(request)
        self.assertEqual(context['observations'], [evidence])
        self.assertEqual([s['observation_refs'] for s in context['strategies']], [[0], [0]])

    def test_matching_best_source_uses_parent_reference(self):
        request = replace(self.request(), memory={'best': {'id': 'c1', 'rtl': self.request().parent_rtl,
                          'area': 75.278, 'logic_depth': 6}})
        best = compact_context(request)['memory']['best']
        self.assertNotIn('rtl', best)
        self.assertEqual(best['rtl_ref'], 'parent_rtl')
        self.assertEqual(best['logic_depth_ref'], 'metrics.parent_depth')
