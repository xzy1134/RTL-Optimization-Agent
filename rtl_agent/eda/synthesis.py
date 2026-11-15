"""One fixed synthesis recipe for baseline and all candidates."""
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .common import (EDAConfig, PIPELINE_VERSION, SynthesisResult, digest, finish,
                     new_run, quote, read_sources, save_json, snapshot, validate)
from .yosys_runner import run_yosys, yosys_version


def synthesize_rtl(rtl_file, top, *, config=None, outdir=None, extra_sources=()):
    started = time.perf_counter()
    run = new_run(outdir, "synth")
    config = config or EDAConfig.from_tools_env()
    result = SynthesisResult("ERROR", "", str(run), timing_metric=config.timing_metric)
    try:
        validate(top, config)
        lib = Path(config.liberty_file).resolve()
        if not config.liberty_file or not lib.is_file():
            raise ValueError("A readable Liberty file is required")
        paths = snapshot(rtl_file, extra_sources, run, "sources")
        # Freeze the library as well: concurrent edits cannot change this run.
        frozen_lib = run / "library.lib"
        frozen_lib.write_bytes(lib.read_bytes())
        library_setup = (f"read_liberty -lib {quote(frozen_lib)}\n"
                         f"write_json {quote(run / 'library_cells.json')}\n")
        recipe = (f"hierarchy -check -top {top}\nflatten -noscopeinfo\nproc\nopt\ntechmap\nopt\n"
                  f"abc -liberty {quote(frozen_lib)}\n"
                  f"stat -liberty {quote(frozen_lib)}\n"
                  f"write_json {quote(run / 'mapped.json')}\nselect -module {top}\nltp\n")
        version = yosys_version(config)
        comparable = {"pipeline_version": PIPELINE_VERSION, "yosys_version": version,
                      "liberty_sha256": digest(frozen_lib), "top": top,
                      "timing_metric": config.timing_metric,
                      "recipe": (library_setup + "<SOURCES>\n" + recipe)
                      .replace(str(frozen_lib), "<LIBERTY>")
                      .replace(str(run / 'mapped.json'), "<NETLIST>")
                      .replace(str(run / 'library_cells.json'), "<LIBRARY_CELLS>")}
        result.config_fingerprint = hashlib.sha256(json.dumps(comparable, sort_keys=True).encode()).hexdigest()
        save_json(run / "config.json", {**asdict(config), **comparable,
                  "original_liberty": str(lib), "config_fingerprint": result.config_fingerprint})
        execution = run_yosys(config, library_setup + read_sources(paths) + recipe, run,
                              timeout=config.timeout_seconds - (time.perf_counter() - started))
        if execution.timed_out:
            result.status, result.message = "TIMEOUT", "Synthesis time budget exhausted"
        elif execution.returncode != 0:
            result.message = execution.error or "Yosys synthesis failed; see yosys.log"
        else:
            log = Path(execution.log).read_text(errors="replace")
            areas = re.findall(r"Chip area for module .*?:\s*([0-9.eE+-]+)", log)
            depths = re.findall(r"Longest topological path.*?\(length=(\d+)\)", log)
            netlist = json.loads((run / "mapped.json").read_text())["modules"]
            library_cells = json.loads((run / "library_cells.json").read_text())["modules"]
            cells = netlist[top].get("cells", {})
            unmapped = sorted({c["type"] for c in cells.values()
                               if c["type"] not in library_cells})
            if unmapped:
                raise ValueError("Incomplete Liberty mapping; area would be incomplete: " + ", ".join(unmapped))
            if (not areas and cells) or len(depths) != 1:
                raise ValueError("Missing or ambiguous area/depth metrics in Yosys output")
            result.area, result.logic_depth = float(areas[-1]) if areas else 0.0, int(depths[0])
            if result.area < 0 or not math.isfinite(result.area):
                raise ValueError("Invalid area metric")
            result.status, result.message = "OK", "Mapped cell area and topological logic depth (not time)"
    except TimeoutError as exc:
        result.status, result.message = "TIMEOUT", str(exc)
    except (OSError, ValueError) as exc:
        result.message = str(exc)
    return finish(run, result, started)
