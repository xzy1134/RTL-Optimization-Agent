"""Exercise bounded search memory on three existing combinational benchmarks."""
import argparse
import json
from pathlib import Path
import time

from rtl_agent.agent.graph import run_search
from rtl_agent.agent.state import SearchBudget
from rtl_agent.eda import EDAConfig, synthesize_rtl
from rtl_agent.eda.common import ROOT, new_run, save_json
from rtl_agent.llm import MockLLM, ModelResponse
from rtl_agent.llm.deepseek_client import DeepSeekClient

CASES = {
    'popcount3': ('popcount3/popcount3_v1_behavioral.v', 'TopModule', 'popcount3/popcount3_v3_halfadder.v'),
    'addsubz': ('addsubz/addsubz_v1_behavioral.v', 'TopModule', 'addsubz/addsubz_v2_ripple.v'),
    'barrel': ('barrel/barrel_v1_mux.v', 'barrel_shifter', None),
}


def run_benchmarks(backend='mock', cases=None):
    # Construct before starting so a missing key fails without an API request.
    provider = DeepSeekClient(max_retries=0) if backend == 'deepseek' else None
    started = time.perf_counter()
    root = new_run(None, 'bench-memory')
    config = EDAConfig.from_tools_env(timeout_seconds=20)
    rows = []
    for name in cases or CASES:
        original, top, alternative = CASES[name]
        source = ROOT/'case/bench'/original
        baseline = synthesize_rtl(source, top, config=config, outdir=root)
        if baseline.status != 'OK':
            rows.append({'case': name, 'status': baseline.status, 'baseline_run': baseline.run_dir})
            continue
        if provider:
            model = provider
        else:
            # Offline fixtures only, not LLM-generated optimization results.
            candidate = ((ROOT/'case/bench'/alternative).read_text() if alternative else
                'module barrel_shifter(input [7:0] in,input [2:0] ctrl,output [7:0] out);\n'
                'assign out=in >> ctrl;\nendmodule\n')
            model = MockLLM([ModelResponse(rtl=candidate, description='Offline equivalent structure fixture'),
                             ModelResponse(action='NO_CHANGE', description='Offline scenario complete')])
        state = run_search(model, original_rtl=source, top=top, config=config, outdir=root,
                           depth_limit=baseline.logic_depth,
                           budget=SearchBudget(3, 3, 3, 120, 3, 30000))
        report = json.loads(Path(state['report_path']).read_text())
        rows.append({'case': name, 'status': report['outcome'], 'depth_limit': baseline.logic_depth,
                     'baseline_area': report['baseline'].get('area'),
                     'baseline_depth': report['baseline'].get('logic_depth'),
                     'best_area': report['best']['area'] if report['best'] else None,
                     'best_depth': report['best']['logic_depth'] if report['best'] else None,
                     'model_usage': report['model_usage'], 'runtime_seconds': report['runtime_seconds'],
                     'stop_reason': report['stop_reason'], 'report_path': state['report_path']})
    summary = {'backend': backend, 'results': rows, 'runtime_seconds': time.perf_counter()-started,
               'per_case_budget': {'candidates':3, 'rounds':3, 'requests':3, 'tokens':30000, 'seconds':120},
               'note': 'Mock uses hand-prepared fixtures; only DeepSeek mode measures live model behavior.'}
    save_json(root/'summary.json', summary)
    return root/'summary.json'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', choices=('mock', 'deepseek'), default='mock')
    p.add_argument('--case', choices=tuple(CASES), action='append')
    args = p.parse_args()
    try:
        path = run_benchmarks(args.backend, args.case)
    except ValueError as exc:
        p.error(str(exc))
    print(path.read_text())
    print(f'Summary: {path}')


if __name__ == '__main__':
    main()
