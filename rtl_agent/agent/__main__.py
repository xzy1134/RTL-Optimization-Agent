"""Run bounded RTL search with Mock or DeepSeek."""
import argparse
import json
from pathlib import Path

from rtl_agent.eda import EDAConfig, synthesize_rtl
from rtl_agent.llm import MockLLM, ModelResponse
from rtl_agent.llm.deepseek_client import DeepSeekClient
from .graph import run_search
from .state import SearchBudget


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('rtl')
    p.add_argument('--top', required=True)
    feedback = p.add_mutually_exclusive_group()
    feedback.add_argument('--diagnostics', action='store_true', help='Enable M5 diagnostic feedback and routing')
    feedback.add_argument('--legacy-feedback', action='store_true', help='Compatibility alias for default historical feedback')
    p.add_argument('--backend', choices=('mock', 'deepseek'), default='mock')
    p.add_argument('--responses', help='Mock JSON sequence; required for --backend mock')
    p.add_argument('--model', help='Override model from project .env or environment')
    p.add_argument('--api-url', help='Override DeepSeek chat-completions endpoint')
    p.add_argument('--api-timeout', type=float, default=45)
    p.add_argument('--max-retries', type=int, default=1)
    p.add_argument('--max-output-tokens', type=int, default=2048)
    p.add_argument('--max-api-requests', type=int, default=8)
    p.add_argument('--max-tokens', type=int, default=50000)
    depth = p.add_mutually_exclusive_group()
    depth.add_argument('--depth-limit', type=int)
    depth.add_argument('--depth-limit-baseline', action='store_true',
                       help='Use original RTL synthesis depth as the fixed search limit')
    p.add_argument('--max-candidates', type=int, default=8)
    p.add_argument('--max-rounds', type=int, default=8)
    p.add_argument('--patience', type=int, default=4)
    p.add_argument('--runtime', type=float, default=60)
    p.add_argument('--eda-timeout', type=float, default=20)
    p.add_argument('--outdir')
    p.add_argument('--tools-env')
    p.add_argument('--source', action='append', default=[])
    args = p.parse_args()
    config = EDAConfig.from_tools_env(args.tools_env, timeout_seconds=args.eda_timeout)
    if args.depth_limit_baseline:
        baseline = synthesize_rtl(args.rtl, args.top, config=config, outdir=args.outdir,
                                  extra_sources=args.source)
        if baseline.status != 'OK' or type(baseline.logic_depth) is not int:
            p.error('Cannot establish baseline logic depth; inspect synthesis artifacts at ' + baseline.run_dir)
        args.depth_limit = baseline.logic_depth
    if args.backend == 'mock':
        if not args.responses:
            p.error('--responses is required for Mock')
        sequence = Path(args.responses).resolve()
        responses = []
        for item in json.loads(sequence.read_text()):
            fields = dict(item)
            if 'rtl_file' in fields:
                fields['rtl'] = (sequence.parent / fields.pop('rtl_file')).read_text()
            responses.append(ModelResponse(**fields))
        model = MockLLM(responses)
    else:
        try:
            model = DeepSeekClient(model=args.model, api_url=args.api_url, max_retries=args.max_retries,
                                   max_output_tokens=args.max_output_tokens, request_timeout=args.api_timeout)
        except ValueError as exc:
            p.error(str(exc))
    state = run_search(model, original_rtl=args.rtl, top=args.top,
                       config=config,
                       budget=SearchBudget(args.max_candidates, args.max_rounds, args.patience, args.runtime,
                                           args.max_api_requests, args.max_tokens),
                       diagnostics=args.diagnostics, depth_limit=args.depth_limit, outdir=args.outdir, extra_sources=args.source)
    print(Path(state['report_path']).read_text())


if __name__ == '__main__':
    main()
