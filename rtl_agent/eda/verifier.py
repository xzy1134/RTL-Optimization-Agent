"""Conservative combinational proof via full-output miter, without internal matching."""
from dataclasses import asdict
import json
from pathlib import Path
import time

from .common import (EDAConfig, VerificationResult, finish, new_run, quote,
                     read_sources, save_json, snapshot, validate)
from .yosys_runner import run_yosys, yosys_version

# All other cells (including FFs, latches, memories, black boxes and formal
# assumptions) are explicitly outside the M1 proof contract.
COMB_CELLS = {
    "$not", "$pos", "$neg", "$and", "$or", "$xor", "$xnor", "$reduce_and",
    "$reduce_or", "$reduce_xor", "$reduce_xnor", "$reduce_bool", "$logic_not",
    "$logic_and", "$logic_or", "$shl", "$shr", "$sshl", "$sshr", "$shift", "$shiftx",
    "$lt", "$le", "$eq", "$ne", "$eqx", "$nex", "$ge", "$gt", "$add", "$sub",
    "$mul", "$div", "$mod", "$divfloor", "$modfloor", "$pow", "$mux", "$pmux",
    "$bmux", "$demux", "$concat", "$slice", "$alu", "$macc", "$fa", "$lcu",
}


def unsupported(module):
    reasons = []
    for cell in module.get("cells", {}).values():
        if cell["type"] not in COMB_CELLS:
            reasons.append("unsupported cell " + cell["type"])
        if any(bit in ("x", "z") for bits in cell.get("connections", {}).values() for bit in bits):
            reasons.append("undefined/high-impedance cell connection")
    if module.get("memories"):
        reasons.append("memory")
    for port in module["ports"].values():
        if port["direction"] == "inout":
            reasons.append("bidirectional port")
        if any(bit in ("x", "z") for bit in port["bits"]):
            reasons.append("undefined/high-impedance port")
    return sorted(set(reasons))


def interface(module):
    return {name: (p["direction"], len(p["bits"]), p.get("signed", 0),
                   p.get("offset", 0), p.get("upto", 0)) for name, p in module["ports"].items()}


def verify_rtl(original_rtl, candidate_rtl, top, *, config=None, outdir=None,
               original_sources=(), candidate_sources=()):
    started = time.perf_counter()
    run = new_run(outdir, "verify")
    config = config or EDAConfig.from_tools_env()
    result = VerificationResult("ERROR", "", str(run))
    try:
        validate(top, config)
        gold = snapshot(original_rtl, original_sources, run, "gold_sources")
        gate = snapshot(candidate_rtl, candidate_sources, run, "candidate_sources")
        save_json(run / "config.json", {**asdict(config), "yosys_version": yosys_version(config),
                                        "proof": "full-output combinational miter SAT", "top": top,
                                        "assumptions": result.assumptions})
        # Separate design stores prevent duplicate helper/top module definitions.
        script = ""
        for name, paths in (("gold", gold), ("gate", gate)):
            script += (read_sources(paths) + f"hierarchy -check -top {top}\nproc\nflatten -noscopeinfo\n"
                       "check -assert\n"
                       f"rename {top} {name}\nselect -module {name}\n"
                       f"write_json {quote(run / (name + '.json'))}\n"
                       f"select -clear\ndesign -stash {name}\ndesign -reset-vlog\n")
        script += "design -copy-from gold -as gold gold\ndesign -copy-from gate -as gate gate\n"
        script += f"select -clear\nwrite_rtlil {quote(run / 'prepared.il')}\n"
        def execute(script_text, name):
            return run_yosys(config, script_text, run, name,
                             config.timeout_seconds - (time.perf_counter() - started))
        prep = execute(script, "prepare")
        if prep.timed_out:
            result.status, result.message = "UNKNOWN", "Preparation timeout"
            return finish(run, result, started)
        if prep.returncode != 0:
            result.message = prep.error or "RTL parsing/elaboration/check failed; see prepare.log"
            return finish(run, result, started)
        gm = json.loads((run / "gold.json").read_text())["modules"]["gold"]
        cm = json.loads((run / "gate.json").read_text())["modules"]["gate"]
        if interface(gm) != interface(cm):
            result.message = "Top-level interface mismatch (names/directions/widths/signedness/ranges)"
            return finish(run, result, started)
        if gm.get("parameter_default_values", {}) != cm.get("parameter_default_values", {}):
            result.message = "Top-level parameter defaults changed"
            return finish(run, result, started)
        if not any(p["direction"] == "output" for p in gm["ports"].values()):
            result.message = "No output ports to prove"
            return finish(run, result, started)
        reasons = unsupported(gm) + unsupported(cm)
        if reasons:
            result.status, result.message = "UNKNOWN", "; ".join(sorted(set(reasons)))
            return finish(run, result, started)
        proof = (f"read_rtlil {quote(run / 'prepared.il')}\n"
                 "miter -equiv -make_outputs -flatten gold gate miter\n"
                 "hierarchy -top miter\nselect -module miter\n"
                 f"sat -enable_undef -set-def-inputs -prove trigger 0 -show-inputs -show-outputs -dump_json {quote(run / 'counterexample.json')}\n")
        execution = execute(proof, "proof")
        log = Path(execution.log).read_text(errors="replace")
        if execution.timed_out:
            result.status, result.message = "UNKNOWN", "Formal proof timeout; no equivalence conclusion"
        elif execution.returncode != 0:
            result.message = execution.error or "Yosys proof execution failed; see proof.log"
        elif "SAT proof finished - no model found: SUCCESS!" in log:
            result.status, result.message = "PASS", "All output bits proved equivalent for all binary input valuations"
        elif "SAT proof finished - model found: FAIL!" in log and (run / "counterexample.json").is_file():
            result.status, result.message = "FAIL", "SAT found an output-mismatch counterexample"
            result.counterexample = str(run / "counterexample.json")
        else:
            result.status, result.message = "UNKNOWN", "No recognized conclusive SAT result"
    except TimeoutError as exc:
        result.status, result.message = "UNKNOWN", str(exc)
    except (OSError, ValueError, KeyError) as exc:
        result.message = str(exc)
    return finish(run, result, started)
