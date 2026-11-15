"""Inspect an existing RTL and rank strategies. No candidate generation or API calls."""
import argparse
from dataclasses import asdict
import json

from rtl_agent.analysis import analyze_rtl, analyze_yosys_report
from rtl_agent.eda import EDAConfig, synthesize_rtl
from rtl_agent.eda.common import new_run, save_json
from . import load_strategies, select_candidate_strategies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rtl')
    parser.add_argument('--top', required=True)
    parser.add_argument('--depth-limit', type=int)
    parser.add_argument('--synthesis-run', help='Existing M1 run; otherwise synthesize the unchanged baseline')
    parser.add_argument('--tools-env')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--outdir')
    parser.add_argument('--source', action='append', default=[])
    args = parser.parse_args()
    if args.depth_limit is not None and args.depth_limit < 0:
        parser.error('--depth-limit must be nonnegative logic levels')
    cfg = EDAConfig.from_tools_env(args.tools_env, timeout_seconds=args.timeout)
    run = new_run(args.outdir, 'strategy-demo')
    f = analyze_rtl(args.rtl, args.top, config=cfg, outdir=run, extra_sources=args.source)
    synthesis_run = args.synthesis_run
    if f.status == 'OK' and not synthesis_run:
        synthesis_run = synthesize_rtl(args.rtl, args.top, config=cfg, outdir=run,
                                       extra_sources=args.source).run_dir
    report = analyze_yosys_report(synthesis_run) if synthesis_run else None
    decisions = select_candidate_strategies(load_strategies(), f, report, depth_limit=args.depth_limit)
    summary = {'features': asdict(f), 'synthesis': asdict(report) if report else None,
               'depth_limit': args.depth_limit, 'recommendations': [asdict(d) for d in decisions],
               'note': 'Search suggestions only; no candidate, LLM call or optimization experiment',
               'run_dir': str(run)}
    save_json(run / 'summary.json', summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if f.status == 'OK' and report and report.status == 'OK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
