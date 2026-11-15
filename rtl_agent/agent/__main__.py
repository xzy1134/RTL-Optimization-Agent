"""Run the Mock search from a JSON source-candidate sequence."""
import argparse
import json
from pathlib import Path

from rtl_agent.eda import EDAConfig
from rtl_agent.llm import MockLLM, ModelResponse
from .graph import run_search
from .state import SearchBudget


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('rtl')
    p.add_argument('--top', required=True)
    p.add_argument('--responses', required=True, help='JSON list; rtl_file paths are relative to this file')
    p.add_argument('--depth-limit', type=int)
    p.add_argument('--max-candidates', type=int, default=8)
    p.add_argument('--max-rounds', type=int, default=8)
    p.add_argument('--patience', type=int, default=4)
    p.add_argument('--runtime', type=float, default=60)
    p.add_argument('--eda-timeout', type=float, default=20)
    p.add_argument('--outdir')
    p.add_argument('--tools-env')
    p.add_argument('--source', action='append', default=[])
    args = p.parse_args()
    sequence = Path(args.responses).resolve()
    responses = []
    for item in json.loads(sequence.read_text()):
        fields = dict(item)
        if 'rtl_file' in fields:
            fields['rtl'] = (sequence.parent / fields.pop('rtl_file')).read_text()
        responses.append(ModelResponse(**fields))
    state = run_search(MockLLM(responses), original_rtl=args.rtl, top=args.top,
                       config=EDAConfig.from_tools_env(args.tools_env, timeout_seconds=args.eda_timeout),
                       budget=SearchBudget(args.max_candidates, args.max_rounds, args.patience, args.runtime),
                       depth_limit=args.depth_limit, outdir=args.outdir, extra_sources=args.source)
    print(Path(state['report_path']).read_text())


if __name__ == '__main__':
    main()
