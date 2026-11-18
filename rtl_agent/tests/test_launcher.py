import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from rtl_agent.eda.common import ROOT


class LauncherTests(unittest.TestCase):
    def launch(self, answers):
        # Use a fake interpreter. These menu tests never invoke a model or EDA.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT/'run_agent.sh', root/'run_agent.sh')
            interpreter = root/'.venv-m3/bin/python'
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text('#!/usr/bin/python3\nimport json,sys\nprint("STUB_ARGS="+json.dumps(sys.argv[1:]))\n')
            interpreter.chmod(0o755)
            (root/'design space').mkdir()
            (root/'design space/input.v').write_text('module TopModule; endmodule')
            result = subprocess.run(['bash', str(root/'run_agent.sh')], input=answers,
                                    capture_output=True, text=True, timeout=5)
            lines = [s for s in result.stdout.splitlines() if s.startswith('STUB_ARGS=')]
            args = json.loads(lines[0].split('=',1)[1]) if lines else None
            return result, args

    def test_defaults_use_custom_file_and_real_deepseek(self):
        result, args = self.launch('design space/input.v\nTopModule\n')
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('选择测试', result.stdout)
        self.assertNotIn('选择候选来源', result.stdout)
        self.assertEqual(args[:3], ['-B','-m','rtl_agent.agent'])
        self.assertTrue(args[3].endswith('/design space/input.v'))
        self.assertEqual(args[args.index('--top')+1], 'TopModule')
        self.assertEqual(args[args.index('--backend')+1], 'deepseek')
        self.assertIn('--depth-limit-baseline', args)
        self.assertNotIn('--responses', args)
        self.assertEqual(args[args.index('--max-api-requests')+1], '5')

    def test_custom_absolute_path(self):
        result, args = self.launch(f'{ROOT}/case/bench/popcount3/popcount3_v1_behavioral.v\nTopModule\n')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(args[3], str(ROOT/'case/bench/popcount3/popcount3_v1_behavioral.v'))
        self.assertEqual(args[args.index('--backend')+1], 'deepseek')

    def test_bad_selection_or_eof_does_not_launch(self):
        for answers in ('', '\n', 'missing.v\n', 'design space/input.v\n\n',
                        'design space/input.v\n', 'design space/input.v\ninvalid top\n'):
            with self.subTest(answers=answers):
                _, args = self.launch(answers)
                self.assertIsNone(args)

    def test_baseline_depth_option_with_real_eda_and_mock(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'work/rtl_agent') as tmp:
            sequence = Path(tmp)/'responses.json'
            sequence.write_text(json.dumps([{'action': 'NO_CHANGE', 'description': 'Offline CLI check'}]))
            result = subprocess.run([str(ROOT/'.venv-m3/bin/python'), '-B', '-m', 'rtl_agent.agent',
                str(ROOT/'case/bench/popcount3/popcount3_v1_behavioral.v'), '--top', 'TopModule',
                '--backend', 'mock', '--responses', str(sequence), '--depth-limit-baseline',
                '--max-rounds', '1', '--outdir', tmp], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['depth_limit'], report['baseline']['logic_depth'])
            self.assertEqual(report['depth_limit'], 2)
            self.assertEqual(report['model_usage']['real_api_calls'], 0)
