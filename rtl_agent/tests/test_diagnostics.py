import json
from pathlib import Path
import tempfile
import unittest

from rtl_agent.analysis.diagnostics import paths
from rtl_agent.agent.graph import run_search
from rtl_agent.agent.state import SearchBudget
from rtl_agent.eda import EDAConfig, synthesize_rtl
from rtl_agent.eda.common import ROOT
from rtl_agent.llm import MockLLM, ModelResponse


class DiagnosticsTests(unittest.TestCase):
    def test_aliases_ties_and_damaged_graphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            rtl = Path(tmp)/'t.v'
            rtl.write_text('module t(input [2:1] a, output [1:0] y); assign y=~a; endmodule')
            result = synthesize_rtl(rtl,'t',config=EDAConfig.from_tools_env(),outdir=tmp)
            run = Path(result.run_dir)
            good = paths(run)
            self.assertEqual(good['depth'], result.logic_depth)
            self.assertEqual({p['start'][0] for p in good['paths']},{'a[1]','a[2]'})
            mapped = run/'mapped.json'
            original = mapped.read_text()
            for change in ('cycle','unknown','undriven','missing pin'):
                data = json.loads(original)
                cell = next(iter(data['modules']['t']['cells'].values()))
                pins = cell['connections']
                ip, op = sorted(pins, key=lambda pin:cell['port_directions'][pin])
                if change == 'cycle':
                    pins[ip] = pins[op]
                elif change == 'unknown':
                    cell['type'] = 'unknown'
                elif change == 'undriven':
                    pins[ip] = [9999]
                else:
                    del pins[ip]
                mapped.write_text(json.dumps(data))
                self.assertEqual(paths(run)['status'],'UNSUPPORTED',change)

    def test_real_feedback_and_trigger(self):
        fixtures = ROOT/'rtl_agent/examples'
        with tempfile.TemporaryDirectory() as tmp:
            model = MockLLM([ModelResponse(rtl=(fixtures/name).read_text()) for name in
                             ('mock_wrong.v','mock_same.v','mock_shared.v')])
            state = run_search(model,original_rtl=fixtures/'mock_baseline.v',top='t',
                               config=EDAConfig.from_tools_env(),outdir=tmp,diagnostics=True,depth_limit=6,
                               budget=SearchBudget(3,3,3,30))
            first = model.requests[1].feedback[-1]['diagnosis']
            self.assertTrue(first['evidence']['gold_vs_candidate'])
            self.assertEqual(first['hypothesis'],'unknown')
            area = model.requests[2].feedback[-1]['diagnosis']
            self.assertTrue(area['evidence']['same_histogram'])
            self.assertNotIn('paths', area['evidence'])
            self.assertFalse((Path(state['run_dir'])/'round-0003/diagnosis.json').exists())

    def test_real_depth_trigger_compares_all_versions(self):
        fixtures = ROOT/'rtl_agent/examples'
        with tempfile.TemporaryDirectory() as tmp:
            model = MockLLM([ModelResponse(rtl=(fixtures/'mock_shared.v').read_text())])
            state = run_search(model,original_rtl=fixtures/'mock_priority.v',top='t',
                               config=EDAConfig.from_tools_env(),outdir=tmp,diagnostics=True,depth_limit=5,
                               budget=SearchBudget(1,1,1,30))
            diagnosis = json.loads((Path(state['run_dir'])/'round-0001/diagnosis.json').read_text())
            self.assertEqual(diagnosis['measured']['reason'],'DEPTH_LIMIT')
            self.assertEqual([diagnosis['evidence'][v]['depth'] for v in ('original','parent','candidate')],[5,5,6])

    def test_switch_after_two_area_failures_and_legacy_control(self):
        fixtures = ROOT/'rtl_agent/examples'
        source = (fixtures/'mock_same.v').read_text()
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                model = MockLLM([ModelResponse(rtl=source), ModelResponse(rtl=source+'\n'),
                                 ModelResponse(action='NO_CHANGE')])
                state = run_search(model, original_rtl=fixtures/'mock_baseline.v', top='t',
                                   config=EDAConfig.from_tools_env(), outdir=tmp, diagnostics=enabled,
                                   budget=SearchBudget(3,3,3,30))
                request = model.requests[-1]
                self.assertEqual(state['switch_location'], enabled)
                self.assertEqual('objective' in request.memory, enabled)
                self.assertEqual('diagnosis' in request.feedback[-1], enabled)
                if enabled:
                    self.assertIn('another cone', request.memory['objective']['inspect'])
