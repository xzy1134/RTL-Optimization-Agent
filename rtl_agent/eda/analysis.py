"""Independent generic-netlist inspection. The M1 score/proof recipes are unchanged."""
from dataclasses import asdict
import json
from pathlib import Path
import time

from .common import EDAConfig, new_run, validate, snapshot, read_sources, quote, save_json
from .yosys_runner import run_yosys, yosys_version
from rtl_agent.analysis.rtl_features import RTLFeatures, analyze_netlist


def analyze_rtl(rtl_file, top, *, config=None, outdir=None, extra_sources=()):
    started = time.perf_counter()
    config = config or EDAConfig.from_tools_env()
    run = new_run(outdir, 'analyze')
    result = RTLFeatures(top, status='ERROR', run_dir=str(run))
    try:
        validate(top, config)
        paths = snapshot(rtl_file, extra_sources, run, 'sources')
        version = yosys_version(config)
        script = read_sources(paths) + (f'hierarchy -check -top {top}\nproc\nflatten -noscopeinfo\n'
                     f'check -assert\nwrite_json {quote(run / "generic.json")}\n')
        save_json(run / 'config.json', {'yosys_version': version, 'top': top,
                  'purpose': 'structural hints only; not a synthesis score or proof'})
        execution = run_yosys(config, script, run, 'analysis',
                              timeout=config.timeout_seconds - (time.perf_counter() - started))
        if execution.timed_out:
            result.status, result.message = 'TIMEOUT', 'Structure analysis timed out'
        elif execution.returncode != 0:
            result.message = execution.error or 'Yosys analysis failed; see analysis.log'
        else:
            text = (run / 'generic.json').read_text()
            # Stable evidence identifiers across runs; retain the raw netlist on disk.
            for path in paths:
                text = text.replace(json.dumps(str(path))[1:-1], 'source/' + path.name)
            result = analyze_netlist(json.loads(text), top)
            result.run_dir = str(run)
            result.source_hashes = [item['sha256'] for item in json.loads((run / 'sources/manifest.json').read_text())]
    except TimeoutError as exc:
        result.status, result.message = 'TIMEOUT', str(exc)
    except (OSError, ValueError, KeyError) as exc:
        result.message = str(exc)
    save_json(run / 'features.json', asdict(result))
    return result
