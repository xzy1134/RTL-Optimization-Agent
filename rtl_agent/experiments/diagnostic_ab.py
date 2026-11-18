"""Paid, bounded comparison: four serial searches, at most twelve requests total."""
import json
from pathlib import Path

from rtl_agent.agent.graph import run_search
from rtl_agent.agent.state import SearchBudget
from rtl_agent.eda import EDAConfig
from rtl_agent.eda.common import ROOT, new_run, save_json
from rtl_agent.llm.deepseek_client import DeepSeekClient


def main():
    root = new_run(None, 'diagnostic-ab')
    print(root, flush=True)
    rows = []
    for name, filename, top, limit in [
        ('barrel','barrel_v1_mux.v','barrel_shifter',4),
        ('addsubz','addsubz_v1_behavioral.v','TopModule',10)]:
        for enabled in (False, True):
            state = run_search(DeepSeekClient(max_retries=0), diagnostics=enabled,
                               original_rtl=ROOT/'case/bench'/name/filename, top=top,
                               config=EDAConfig.from_tools_env(), depth_limit=limit, outdir=root,
                               budget=SearchBudget(3,3,3,120,3,40000))
            report = json.loads(Path(state['report_path']).read_text())
            records = [json.loads(Path(p).read_text()) for p in report['history']]
            invalid = sum(r['reason'] not in ('ACCEPTED','NO_AREA_IMPROVEMENT','DEPTH_LIMIT','NO_CHANGE')
                          for r in records)
            row = dict(case=name, diagnostics=enabled, fingerprint=report['config_fingerprint'],
                       formal_pass=report['passed_count'], candidates=report['candidate_count'],
                       invalid_or_duplicate=invalid, rounds=len(records), reasons=[r['reason'] for r in records],
                       improvement=report['area_improvement_percent'], usage=report['model_usage'],
                       seconds=report['runtime_seconds'], diagnosis_seconds=report['diagnostic_seconds'],
                       report=state['report_path'])
            rows.append(row)
            save_json(root/'comparison.json', rows)
            print(name,enabled,row['reasons'],flush=True)
            if report['stop_reason'] in ('NETWORK_ERROR','MODEL_TIMEOUT','HTTP_401','HTTP_402','HTTP_403','USAGE_MISSING'):
                return


if __name__ == '__main__':
    main()
