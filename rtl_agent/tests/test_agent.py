from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from rtl_agent.agent.graph import run_search, build_graph
from rtl_agent.agent.state import SearchBudget
from rtl_agent.agent.evaluator import assess
from rtl_agent.eda import EDAConfig
from rtl_agent.eda.common import ROOT, digest
from rtl_agent.llm import MockLLM, ModelResponse

EXAMPLES = ROOT / 'rtl_agent/examples'


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='agent-tests-', dir=ROOT / 'work/rtl_agent'))
        print(f'\nAgent test artifacts: {cls.root}', flush=True)
        cls.config = EDAConfig.from_tools_env(timeout_seconds=10)

    def response(self, name):
        return ModelResponse(rtl=(EXAMPLES / name).read_text(), description=name)

    def search(self, responses=(), baseline='mock_baseline.v', **kwargs):
        model = kwargs.pop('model', MockLLM(responses))
        budget = kwargs.pop('budget', SearchBudget(10, 10, 8, 30))
        source = EXAMPLES / baseline
        original_hash = digest(source)
        state = run_search(model, original_rtl=source, top='t', config=self.config,
                           outdir=self.root, budget=budget, **kwargs)
        self.assertEqual(digest(source), original_hash)
        report = json.loads(Path(state['report_path']).read_text())
        history = [json.loads(Path(p).read_text()) for p in state['history']]
        return state, report, history, model

    def test_actual_improvement_failures_duplicate_and_artifacts(self):
        sequence = [self.response('mock_same.v'), self.response('mock_wrong.v'),
                    ModelResponse(rtl='module t(input a,output y); assign y=; endmodule'),
                    replace(self.response('mock_shared.v'), strategy_id='resource_sharing'), self.response('mock_shared.v')]
        s, r, h, model = self.search(sequence, depth_limit=6)
        self.assertEqual([c['reason'] for c in h], ['NO_AREA_IMPROVEMENT', 'FORMAL_FAIL', 'PRECHECK_ERROR',
                                                   'ACCEPTED', 'DUPLICATE', 'NO_CHANGE'])
        self.assertEqual(r['candidate_count'], 5)
        self.assertEqual(r['passed_count'], 2)
        self.assertEqual(r['stop_reason'], 'NO_CHANGE')
        self.assertAlmostEqual(r['baseline']['area'], 75.278, places=3)
        self.assertAlmostEqual(r['best']['area'], 42.560, places=3)
        self.assertEqual(r['best']['logic_depth'], 6)
        self.assertEqual(r['best']['id'], h[3]['candidate_id'])
        self.assertEqual(h[3]['strategy_id'], 'resource_sharing')
        self.assertTrue(Path(r['output_rtl']).is_file())
        self.assertEqual(r['model_usage']['real_api_calls'], 0)
        self.assertTrue(r['depth_constraint_checked'])
        self.assertEqual(h[4]['equivalence']['status'], 'NOT_RUN')
        self.assertEqual(h[1]['synthesis'], {})
        self.assertEqual(h[2]['synthesis'], {})
        self.assertTrue(Path(h[1]['equivalence']['counterexample']).is_file())
        best = json.loads((Path(s['run_dir']) / 'best.json').read_text())
        self.assertEqual(best['rtl_path'], r['output_rtl'])
        for record in h[:5]:
            self.assertTrue(Path(record['rtl_path']).exists())
            self.assertTrue(Path(record['diff_path']).exists())
        self.assertTrue(model.requests[1].feedback)
        self.assertEqual(model.requests[4].parent_rtl, (EXAMPLES/'mock_shared.v').read_text())

    def test_real_depth_violation_does_not_replace_feasible_baseline(self):
        # Measurements are computed again by the graph, not supplied by Mock.
        s, r, h, _ = self.search([self.response('mock_shared.v')], baseline='mock_priority.v', depth_limit=5)
        self.assertTrue(r['baseline_feasible'])
        self.assertEqual(r['baseline']['logic_depth'], 5)
        self.assertEqual(h[0]['equivalence']['status'], 'PASS')
        self.assertEqual(h[0]['synthesis']['logic_depth'], 6)
        self.assertLess(h[0]['synthesis']['area'], r['baseline']['area'])
        self.assertEqual(h[0]['reason'], 'DEPTH_LIMIT')
        self.assertFalse(h[0]['accepted'])
        self.assertEqual(r['best']['id'], 'baseline')

    def test_infeasible_baseline_has_no_best(self):
        s, r, h, _ = self.search([self.response('mock_shared.v')], depth_limit=0)
        self.assertFalse(r['baseline_feasible'])
        self.assertIsNone(r['best'])
        self.assertEqual(r['outcome'], 'NO_FEASIBLE_DESIGN')
        self.assertFalse((Path(s['run_dir'])/'best.json').exists())
        self.assertEqual(r['output_rtl'], s['original_path'])

    def test_first_feasible_candidate_after_infeasible_baseline(self):
        _, r, h, _ = self.search([self.response('mock_priority.v')], depth_limit=5)
        self.assertFalse(r['baseline_feasible'])
        self.assertTrue(h[0]['accepted'])
        self.assertEqual(r['best']['logic_depth'], 5)

    def test_no_constraint_is_explicit(self):
        _, r, _, _ = self.search([self.response('mock_shared.v')])
        self.assertIsNone(r['depth_limit'])
        self.assertFalse(r['depth_constraint_checked'])

    def test_later_failure_preserves_best_and_history(self):
        _, r, h, _ = self.search([self.response('mock_shared.v'), self.response('mock_wrong.v')])
        self.assertTrue(h[0]['accepted'])
        self.assertEqual(h[1]['reason'], 'FORMAL_FAIL')
        self.assertEqual(r['best']['id'], h[0]['candidate_id'])
        self.assertEqual(h[1]['parent_id'], h[0]['candidate_id'])
        self.assertEqual(h[1]['synthesis'], {})

    def test_max_candidates(self):
        _, r, h, _ = self.search([self.response('mock_shared.v')], budget=SearchBudget(1, 10, 8, 30))
        self.assertEqual(r['stop_reason'], 'MAX_CANDIDATES')
        self.assertEqual(len(h), 1)

    def test_max_rounds_and_patience(self):
        _, r, _, _ = self.search([self.response('mock_same.v')]*3, budget=SearchBudget(10, 1, 8, 30))
        self.assertEqual(r['stop_reason'], 'MAX_ROUNDS')
        _, r, _, _ = self.search([self.response('mock_same.v')]*3, budget=SearchBudget(10, 10, 1, 30))
        self.assertEqual(r['stop_reason'], 'NO_IMPROVEMENT')

    def test_runtime_before_baseline(self):
        _, r, h, model = self.search(budget=SearchBudget(10, 10, 8, 0.000001))
        self.assertEqual(r['stop_reason'], 'RUNTIME_LIMIT')
        self.assertFalse(model.requests)
        self.assertEqual(h, [])

    def test_model_runtime_budget(self):
        _, r, h, _ = self.search(model=MockLLM([self.response('mock_shared.v')], delay_seconds=10),
                                 budget=SearchBudget(10, 10, 8, 1.5))
        self.assertEqual(r['stop_reason'], 'RUNTIME_LIMIT')
        self.assertEqual(h[0]['reason'], 'MODEL_TIMEOUT')
        self.assertLess(r['runtime_seconds'], 3)

    def test_no_change(self):
        _, r, h, _ = self.search([ModelResponse(action='NO_CHANGE')])
        self.assertEqual(r['stop_reason'], 'NO_CHANGE')
        self.assertEqual(r['candidate_count'], 0)
        self.assertEqual(r['outcome'], 'BASELINE_RETAINED')

    def test_all_strategies_unavailable(self):
        source = self.root / 'wire.v'
        source.write_text('module t(input a,output y); assign y=a; endmodule')
        model = MockLLM()
        s = run_search(model, original_rtl=source, top='t', config=self.config, outdir=self.root)
        self.assertEqual(s['stop_reason'], 'NO_AVAILABLE_STRATEGIES')
        self.assertEqual(model.requests, [])

    def test_unknown_is_not_synthesized(self):
        unknown = (EXAMPLES/'mock_shared.v').read_text().replace('assign y=x*z;', "assign y=1'bx;")
        _, r, h, _ = self.search([ModelResponse(rtl=unknown)])
        self.assertEqual(h[0]['equivalence']['status'], 'UNKNOWN')
        self.assertFalse(h[0]['accepted'])
        self.assertEqual(h[0]['synthesis'], {})

    def test_interface_change_and_strategy_rejection(self):
        wrong_interface = (EXAMPLES/'mock_shared.v').read_text().replace('parameter W=3', 'parameter W=4')
        _, r, h, _ = self.search([ModelResponse(rtl=wrong_interface),
            ModelResponse(strategy_id='fsm_optimization', rtl=(EXAMPLES/'mock_shared.v').read_text())])
        self.assertEqual(h[0]['reason'], 'INTERFACE_MISMATCH')
        self.assertEqual(h[1]['reason'], 'STRATEGY_NOT_ADMITTED')

    def test_invalid_budgets(self):
        with self.assertRaises(ValueError):
            self.search(budget=SearchBudget(max_candidates=0))

    def test_evaluator_rejects_configuration_and_metric_errors(self):
        proof = {'status': 'PASS'}
        syn = {'status': 'OK', 'area': 1, 'logic_depth': 1, 'timing_metric': 'logic_depth', 'config_fingerprint': 'a'}
        self.assertEqual(assess(proof, syn, 'b', None, None)['reason'], 'CONFIG_MISMATCH')
        self.assertFalse(assess(proof, {**syn, 'area': None}, 'a', None, None)['eligible'])
        self.assertFalse(assess({'status': 'UNKNOWN'}, syn, 'a', None, None)['accepted'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
