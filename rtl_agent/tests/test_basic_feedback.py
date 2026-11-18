"""Offline checks for experimental A and the default graph entry point."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rtl_agent.agent.graph import run_search
from rtl_agent.agent.state import SearchBudget
from rtl_agent.eda import EDAConfig
from rtl_agent.eda.common import ROOT
from rtl_agent.llm import MockLLM, ModelResponse
from rtl_agent.llm.client import ModelRequest
from rtl_agent.llm.prompt_builder import build_messages


class BasicFeedbackTests(unittest.TestCase):
    def test_archived_a_is_reproduced_exactly(self):
        path = ROOT/'work/rtl_agent/addsubz-ablation-oxdrtb32/A-messages.json'
        if not path.exists():
            self.skipTest('Local experiment archive is unavailable')
        expected = json.loads(path.read_text())
        request = ModelRequest(**json.loads(expected[1]['content'].split('\n',1)[1]), feedback_mode='basic')
        self.assertEqual(build_messages(request), expected)

    def test_default_graph_does_not_diagnose_or_switch_from_history(self):
        examples = ROOT/'rtl_agent/examples'
        model = MockLLM([ModelResponse(rtl=(examples/'mock_same.v').read_text()),
                         ModelResponse(rtl=(examples/'mock_same.v').read_text()+'\n'),
                         ModelResponse(action='NO_CHANGE')])
        with tempfile.TemporaryDirectory() as tmp, patch('rtl_agent.agent.nodes.diagnose',
                side_effect=AssertionError('Default must not call diagnostics')):
            state = run_search(model, original_rtl=examples/'mock_baseline.v', top='t',
                               config=EDAConfig.from_tools_env(), outdir=tmp,
                               budget=SearchBudget(3,3,3,30))
            self.assertFalse(state['diagnostics'])
            self.assertFalse(state['switch_location'])
            self.assertEqual(state['parent']['id'], 'baseline')
            self.assertFalse(list(Path(tmp).rglob('diagnosis.json')))
            request = model.requests[-1]
            self.assertEqual(request.feedback_mode, 'historical')
            context = json.loads(build_messages(request)[1]['content'].split('\n',1)[1])
            self.assertTrue(context['strategies'])
            self.assertIn('baseline', context['memory'])
            self.assertIn('best', context['memory'])
            self.assertEqual(context['parent_rtl_ref'], 'original_rtl')
            self.assertEqual(context['original_rtl'], request.original_rtl)
            self.assertTrue(all('actual_edit' in f for f in context['feedback']))
            self.assertEqual(context['constraints'], request.constraints)

    def test_historical_success_messages_are_reproduced(self):
        run = ROOT/'work/rtl_agent/deepseek-search-ouw3ksjt'
        if not run.exists():
            self.skipTest('Local historical archive is unavailable')
        for directory in sorted(run.glob('round-*')):
            with self.subTest(round=directory.name):
                request = ModelRequest(**json.loads((directory/'request.json').read_text()))
                expected = json.loads((directory/'messages.json').read_text())
                self.assertEqual(build_messages(request), expected)
