import json
from pathlib import Path
import unittest

from rtl_agent.eda.common import ROOT, digest
from rtl_agent.experiments.bench_memory import CASES, run_benchmarks


class BenchMemoryTests(unittest.TestCase):
    def test_existing_benchmarks_use_real_eda_and_do_not_change_sources(self):
        originals = [ROOT/'case/bench'/v[0] for v in CASES.values()]
        before = [digest(p) for p in originals]
        summary_path = run_benchmarks('mock')
        summary = json.loads(summary_path.read_text())
        self.assertEqual(len(summary['results']), 3)
        self.assertEqual(before, [digest(p) for p in originals])
        print(f'\nBench memory artifacts: {summary_path}', flush=True)
        for row in summary['results']:
            self.assertIn(row['status'], ('BASELINE_RETAINED', 'IMPROVED'))
            self.assertEqual(row['model_usage']['real_api_calls'], 0)
            report = json.loads(Path(row['report_path']).read_text())
            records = [json.loads(Path(p).read_text()) for p in report['history']]
            self.assertEqual(records[0]['equivalence']['status'], 'PASS')
            self.assertEqual(records[0]['synthesis']['status'], 'OK')
            second_request = Path(records[1]['directory'])/'request.json'
            data = json.loads(second_request.read_text())
            self.assertTrue(data['feedback'])
            self.assertIn('accepted', data['feedback'][0])
            self.assertEqual(data['memory']['best']['area'], row['best_area'])
            self.assertLessEqual(row['best_depth'], row['depth_limit'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
