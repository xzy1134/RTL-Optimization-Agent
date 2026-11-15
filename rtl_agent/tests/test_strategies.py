from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from rtl_agent.analysis import analyze_rtl, analyze_yosys_report
from rtl_agent.eda import EDAConfig, synthesize_rtl
from rtl_agent.eda.common import ROOT
from rtl_agent.strategies import load_strategies, StrategyError, select_candidate_strategies


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = ROOT / 'work/rtl_agent'
        root.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix='strategy-tests-', dir=root))
        cls.config = EDAConfig.from_tools_env(timeout_seconds=20)
        cls.strategies = load_strategies()
        cls.features = analyze_rtl(ROOT / 'case/rtl/opt_top.v', 'opt_top', config=cls.config, outdir=cls.root)
        cls.baseline = synthesize_rtl(ROOT / 'case/rtl/opt_top.v', 'opt_top', config=cls.config, outdir=cls.root)
        cls.report = analyze_yosys_report(cls.baseline.run_dir)
        print(f'\nStrategy test artifacts: {cls.root}', flush=True)

    def source(self, text):
        p = self.root / (self._testMethodName + '.v')
        p.write_text(text)
        return analyze_rtl(p, 't', config=self.config, outdir=self.root)

    def decisions(self, features=None, report=None, **kwargs):
        return select_candidate_strategies(self.strategies, features or self.features, report, **kwargs)

    def decision(self, id, features=None, **kwargs):
        return next(r for r in self.decisions(features, **kwargs) if r.id == id)

    def invalid_yaml(self, text):
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            Path(tmp, 'bad.yaml').write_text(text)
            with self.assertRaises(StrategyError):
                load_strategies(tmp)

    def test_load_six(self):
        self.assertEqual(len(self.strategies), 6)
        for strategy in self.strategies:
            self.assertTrue(strategy.source and strategy.risks and strategy.forbidden_changes and strategy.requires)

    def test_malformed_yaml(self):
        self.invalid_yaml('id: [broken')

    def test_missing_fields(self):
        self.invalid_yaml('id: resource_sharing\n')

    def test_duplicate_keys_and_unsafe_tags(self):
        self.invalid_yaml('id: one\nid: two\n')
        self.invalid_yaml('!!python/object/apply:os.system [echo forbidden]')

    def test_duplicate_ids(self):
        strategy = asdict(self.strategies[0])
        # Use safe_dump so tuples use portable sequences.
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            for n in ('a', 'b'):
                Path(tmp, n + '.yaml').write_text(yaml.safe_dump(strategy))
            with self.assertRaisesRegex(StrategyError, 'Duplicate strategy id'):
                load_strategies(tmp)

    def test_invalid_types_and_source(self):
        data = yaml.safe_load((ROOT / 'rtl_agent/strategies/resource_sharing.yaml').read_text())
        for key, value in [('enabled', 'yes'), ('schema_version', True), ('requires', 'not a list'), ('source', [{'url': 'http://example.com'}])]:
            self.invalid_yaml(yaml.safe_dump({**data, key: value}))

    def test_actual_opt_top_connectivity(self):
        self.assertEqual(self.features.status, 'OK')
        self.assertEqual(self.features.cell_counts, {'$mul': 2, '$mux': 1})
        d = self.decision('resource_sharing')
        self.assertTrue(d.allow_llm)
        self.assertEqual(d.confidence, 'high')
        self.assertEqual(d.evidence[0]['details']['width'], 16)
        self.assertTrue(d.unconfirmed_conditions)

    def test_operator_counts_alone_do_not_recommend_sharing(self):
        f = self.source('module t(input [3:0] a,b,c,d,output [7:0] y,z); assign y=a*b; assign z=c*d; endmodule')
        self.assertEqual(f.cell_counts.get('$mul'), 2)
        self.assertFalse(self.decision('resource_sharing', f).allow_llm)

    def test_extra_consumers_lower_confidence(self):
        f = self.source('module t(input [3:0] a,b,c,d,input s,output [7:0] y,z); wire [7:0] p=a*b,q=c*d; assign y=s?p:q; assign z=p; endmodule')
        r = self.decision('resource_sharing', f)
        self.assertTrue(r.allow_llm)
        self.assertEqual(r.confidence, 'medium')
        self.assertTrue(r.evidence[0]['details']['extra_consumers'])

    def test_different_operator_types_not_shared(self):
        f = self.source('module t(input [3:0] a,b,input s,output [3:0] y); assign y=s?(a+b):(a-b); endmodule')
        self.assertFalse(self.decision('resource_sharing', f).allow_llm)

    def test_identical_operands_are_not_a_savings_claim(self):
        f = self.source('module t(input [3:0] a,b,input s,output [7:0] y); wire [7:0] p=a*b,q=a*b; assign y=s?p:q; endmodule')
        r = self.decision('resource_sharing', f)
        self.assertTrue(r.allow_llm)
        self.assertEqual(r.confidence, 'medium')
        self.assertTrue(r.evidence[0]['details']['identical_operands'])

    def test_incompatible_arithmetic_widths(self):
        f = self.source('module t(input [3:0] a,b,input [7:0] c,d,input s,output [15:0] y); wire [7:0] p=a*b; wire [15:0] q=c*d; assign y=s?{8\'b0,p}:q; endmodule')
        self.assertFalse(self.decision('resource_sharing', f).allow_llm)

    def test_fsm_disabled_and_sequential_blocked(self):
        f = self.source('module t(input clk,a,s,output reg y); always @(posedge clk) y<=s?a:~a; endmodule')
        self.assertTrue(f.sequential_cells)
        self.assertTrue(all(not r.allow_llm for r in self.decisions(f)))
        self.assertFalse(self.decision('fsm_optimization', f).backend_satisfied)

    def test_insufficient_capabilities(self):
        r = self.decision('resource_sharing', capabilities={'mapped_area', 'logic_depth'})
        self.assertFalse(r.allow_llm)
        self.assertIn('combinational_equivalence', r.missing_capabilities)

    def test_report_priority_changes_with_depth_pressure(self):
        loose = self.decisions(report=self.report, depth_limit=100)
        tight = self.decisions(report=replace(self.report, logic_depth=100), depth_limit=100)
        self.assertEqual(loose[0].id, 'resource_sharing')
        self.assertEqual(tight[0].id, 'mux_optimization')
        self.assertTrue(all(r.allow_llm for r in tight[:3]))

    def test_deterministic_even_across_analysis_runs(self):
        f = analyze_rtl(ROOT / 'case/rtl/opt_top.v', 'opt_top', config=self.config, outdir=self.root)
        a = [asdict(r) for r in self.decisions(report=self.report, depth_limit=19)]
        b = [asdict(r) for r in self.decisions(f, report=self.report, depth_limit=19)]
        self.assertEqual(a, b)
        self.assertEqual(a, [asdict(r) for r in self.decisions(report=self.report, depth_limit=19)])

    def test_report_metrics_and_source_mismatch(self):
        self.assertEqual(self.report.status, 'OK')
        self.assertAlmostEqual(self.report.area, 764.218, places=3)
        self.assertEqual(self.report.logic_depth, 19)
        self.assertEqual(self.report.mapped_cell_count, sum(self.report.mapped_cell_counts.values()))
        wrong = replace(self.report, source_hashes=['wrong'])
        self.assertTrue(all(not r.allow_llm for r in self.decisions(report=wrong)))
        reordered = replace(self.features, source_hashes=['b', 'a'])
        ordered_report = replace(self.report, source_hashes=['a', 'b'])
        self.assertTrue(all(not r.allow_llm for r in self.decisions(reordered, report=ordered_report)))

    def test_report_missing_and_non_ok(self):
        self.assertEqual(analyze_yosys_report(self.root / 'missing').status, 'ERROR')
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            Path(tmp, 'result.json').write_text(json.dumps({'status': 'ERROR'}))
            self.assertEqual(analyze_yosys_report(tmp).status, 'UNAVAILABLE')

    def test_width_and_arithmetic_chain(self):
        f = self.source('module t(input [3:0] a,b,c,output [3:0] y); wire [7:0] p={4\'b0,a}+{4\'b0,b}; assign y=p+c; endmodule')
        self.assertTrue(self.decision('width_reduction', f).allow_llm)
        self.assertTrue(self.decision('arithmetic_restructuring', f).allow_llm)

    def test_analysis_error_and_no_depth_units_confusion(self):
        f = self.source('module t(input a, output y); assign y=; endmodule')
        self.assertEqual(f.status, 'ERROR')
        self.assertTrue(all(not r.allow_llm for r in self.decisions(f)))
        with self.assertRaises(ValueError):
            self.decisions(depth_limit=1.8)

    def test_undefined_blocked(self):
        f = self.source("module t(input a,b, output y); assign y=a?(b&1'bx):b; endmodule")
        self.assertTrue(f.proof_limitations)
        self.assertTrue(all(not r.allow_llm for r in self.decisions(f)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
