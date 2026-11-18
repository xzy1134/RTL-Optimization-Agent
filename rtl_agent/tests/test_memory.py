import json
from pathlib import Path
import tempfile
import unittest

from rtl_agent.agent.graph import run_search
from rtl_agent.agent.memory import summarize_trial, build_search_memory, RECENT_LIMIT
from rtl_agent.agent.state import SearchBudget
from rtl_agent.eda import EDAConfig
from rtl_agent.eda.common import ROOT
from rtl_agent.llm import MockLLM, ModelResponse

EXAMPLES = ROOT/'rtl_agent/examples'


class MemoryTests(unittest.TestCase):
    def setUp(self):
        root = ROOT/'work/rtl_agent'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_success_failure_and_best_survive_baseline_restart(self):
        shared = ModelResponse(strategy_id='resource_sharing', description='Share operators',
                               rtl=(EXAMPLES/'mock_shared.v').read_text())
        wrong = ModelResponse(description='Wrong function', rtl=(EXAMPLES/'mock_wrong.v').read_text())
        model = MockLLM([shared, wrong, shared, ModelResponse(action='NO_CHANGE')])
        state = run_search(model, original_rtl=EXAMPLES/'mock_baseline.v', top='t',
            config=EDAConfig.from_tools_env(timeout_seconds=10), outdir=self.root,
            depth_limit=6, budget=SearchBudget(5, 5, 5, 30))
        second, third, fourth = model.requests[1:4]
        self.assertTrue(second.feedback[0]['accepted'])
        self.assertEqual(second.feedback[0]['equivalence'], 'PASS')
        self.assertAlmostEqual(second.feedback[0]['candidate_area'], 42.56, places=3)
        self.assertIn('x*z', second.feedback[0]['actual_edit'].replace(' ', ''))
        self.assertEqual(third.parent_rtl, third.original_rtl)
        self.assertEqual(third.memory['best']['id'], 'candidate-0001')
        self.assertEqual(third.memory['best']['rtl'], shared.rtl)
        self.assertEqual(third.feedback[1]['reason'], 'FORMAL_FAIL')
        self.assertIsNone(third.feedback[1]['candidate_area'])
        self.assertEqual(fourth.feedback[-1]['reason'], 'DUPLICATE')
        self.assertNotIn('SAT proof finished', json.dumps(third.feedback))
        self.assertEqual(second.memory['best']['rtl_ref'], 'parent_rtl')
        self.assertEqual(second.memory['recent_trials_ref'], 'feedback')
        self.assertNotIn('recent_trials', second.memory)
        saved = json.loads((Path(state['run_dir'])/'round-0003/request.json').read_text())
        self.assertEqual(saved['memory']['best']['rtl'], shared.rtl)

    def test_success_notes_are_bounded_and_best_still_available(self):
        model = MockLLM([ModelResponse(rtl=(EXAMPLES/'mock_same.v').read_text())]*7)
        state = run_search(model, original_rtl=EXAMPLES/'mock_baseline.v', top='t',
            config=EDAConfig.from_tools_env(timeout_seconds=10), outdir=self.root,
            budget=SearchBudget(7, 7, 8, 30))
        self.assertEqual(len(state['recent_trials']), RECENT_LIMIT)
        self.assertEqual(len(model.requests[-1].feedback), RECENT_LIMIT)
        self.assertEqual(model.requests[-1].memory['older_trials_omitted'], 1)
        self.assertEqual(model.requests[-1].memory['best']['id'], 'baseline')

    def test_summary_uses_tool_values_and_keeps_only_actual_error(self):
        tool = self.root/'tool'
        tool.mkdir()
        (tool/'analysis.log').write_text('Many irrelevant lines\nERROR: syntax error\nASCII ART\n')
        diff = self.root/'change.diff'
        diff.write_text('--- old\n+++ new\n context\n-old logic\n+new logic\n')
        record = {'candidate_id':'c1', 'parent_id':'baseline', 'strategy_id':'resource_sharing',
                  'action':'CANDIDATE', 'accepted':False, 'reason':'PRECHECK_ERROR',
                  'depth_limit':6, 'description':'Model claims an enormous area reduction',
                  'diff_path':str(diff), 'precheck':{'run_dir':str(tool)},
                  'equivalence':{'status':'NOT_RUN'}, 'synthesis':{}}
        summary = summarize_trial(record)
        self.assertIsNone(summary['candidate_area'])
        self.assertEqual(summary['diagnostic'], 'ERROR: syntax error')
        self.assertEqual(summary['actual_edit'], '-old logic\n+new logic')
        self.assertIn('claims', summary['reported_change'])

    def test_no_best_is_not_invented_and_memory_does_not_mutate_state(self):
        path = self.root/'base.v'
        path.write_text('module t; endmodule')
        state = {'baseline':{'id':'baseline','rtl_path':str(path),'area':1,'logic_depth':2},
                 'original_path':str(path), 'parent':{'rtl_path':str(path)},
                 'baseline_proof':{'status':'PASS'}, 'depth_limit':1, 'best':None,
                 'recent_trials':[], 'history':[]}
        original = dict(state)
        memory = build_search_memory(state)
        self.assertIsNone(memory['best'])
        self.assertFalse(memory['baseline']['timing_pass'])
        self.assertEqual(state, original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
