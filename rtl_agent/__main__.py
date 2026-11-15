"""python3 -m rtl_agent {synth,verify} ..."""
import argparse
from dataclasses import asdict
import json

from .eda import EDAConfig, synthesize_rtl, verify_rtl


def main():
    parser = argparse.ArgumentParser(description="M1 Yosys EDA tools")
    parser.add_argument("command", choices=("synth", "verify"))
    parser.add_argument("rtl", help="baseline/original RTL")
    parser.add_argument("candidate", nargs="?", help="candidate RTL (verify)")
    parser.add_argument("--top", required=True)
    parser.add_argument("--tools-env")
    parser.add_argument("--liberty")
    parser.add_argument("--yosys")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--outdir")
    parser.add_argument("--source", action="append", default=[], help="extra original source; repeatable")
    parser.add_argument("--candidate-source", action="append", default=[])
    args = parser.parse_args()
    if args.command == "verify" and not args.candidate:
        parser.error("verify requires a candidate")
    overrides = {"timeout_seconds": args.timeout}
    if args.liberty:
        overrides["liberty_file"] = args.liberty
    if args.yosys:
        overrides["yosys_bin"] = args.yosys
    config = EDAConfig.from_tools_env(args.tools_env, **overrides)
    if args.command == "synth":
        result = synthesize_rtl(args.rtl, args.top, config=config, outdir=args.outdir, extra_sources=args.source)
    else:
        result = verify_rtl(args.rtl, args.candidate, args.top, config=config, outdir=args.outdir,
                            original_sources=args.source, candidate_sources=args.candidate_source)
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    raise SystemExit({"OK": 0, "PASS": 0, "FAIL": 2, "UNKNOWN": 3, "ERROR": 4, "TIMEOUT": 5}[result.status])


if __name__ == "__main__":
    main()
