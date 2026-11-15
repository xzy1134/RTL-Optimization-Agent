"""Read M1 synthesis artifacts without running or changing synthesis."""
from collections import Counter
from dataclasses import dataclass, field
import json
import math
from pathlib import Path


@dataclass
class YosysReport:
    status: str
    message: str = ''
    area: float | None = None
    logic_depth: int | None = None
    timing_metric: str = 'logic_depth'
    config_fingerprint: str | None = None
    source_hashes: list[str] = field(default_factory=list)
    mapped_cell_counts: dict = field(default_factory=dict)
    mapped_cell_count: int = 0
    top: str = ''


def analyze_yosys_report(run_dir):
    run = Path(run_dir)
    try:
        result = json.loads((run / 'result.json').read_text())
        if result['status'] != 'OK':
            return YosysReport('UNAVAILABLE', 'Synthesis did not succeed')
        area, depth = result['area'], result['logic_depth']
        if (type(area) not in (float, int) or not math.isfinite(area) or area < 0 or
                type(depth) is not int or depth < 0 or result['timing_metric'] != 'logic_depth'):
            raise ValueError('Invalid or unsupported metrics; logic_depth is not ns')
        config = json.loads((run / 'config.json').read_text())
        if not result['config_fingerprint'] or result['config_fingerprint'] != config['config_fingerprint']:
            raise ValueError('Inconsistent synthesis configuration fingerprints')
        top = config['top']
        cells = json.loads((run / 'mapped.json').read_text())['modules'][top].get('cells', {})
        counts = dict(sorted(Counter(c['type'] for c in cells.values()).items()))
        hashes = [s['sha256'] for s in json.loads((run / 'sources/manifest.json').read_text())]
        return YosysReport('OK', 'Mapped counts are clues, not RTL optimization proof', area, depth,
                           'logic_depth', result['config_fingerprint'], hashes, counts, len(cells), top)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return YosysReport('ERROR', str(exc))
