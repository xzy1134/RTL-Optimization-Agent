"""Shared configuration, immutable input snapshots and result contracts."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_VERSION = "m1-v1"


@dataclass(frozen=True)
class EDAConfig:
    yosys_bin: str = "/usr/bin/yosys"
    liberty_file: str = ""
    timeout_seconds: float = 60.0
    timing_metric: str = "logic_depth"

    @classmethod
    def from_tools_env(cls, path=None, **overrides):
        values = {}
        p = Path(path or os.environ.get("TOOLS_ENV", ROOT / "tools.env"))
        if p.is_file():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        return cls(**{
            "yosys_bin": values.get("YOSYS_BIN") or os.environ.get("YOSYS_BIN", "/usr/bin/yosys"),
            "liberty_file": values.get("LIB_FILE") or os.environ.get("LIB_FILE", ""),
            **overrides,
        })


@dataclass
class SynthesisResult:
    status: str
    message: str
    run_dir: str
    area: float | None = None
    logic_depth: int | None = None
    timing_metric: str = "logic_depth"
    runtime_seconds: float = 0.0
    config_fingerprint: str | None = None


@dataclass
class VerificationResult:
    status: str
    message: str
    run_dir: str
    runtime_seconds: float = 0.0
    counterexample: str | None = None
    assumptions: str = "Combinational, fully defined RTL; all binary input valuations; no reset assumptions."


def quote(value):
    value = str(value)
    if any(c in value for c in '\n\r\x00'):
        raise ValueError("Control characters are unsupported in Yosys paths")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def validate(top, config):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        raise ValueError("Use an explicit ordinary Verilog top module identifier")
    if not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    if config.timing_metric != "logic_depth":
        raise ValueError("M1 only implements timing_metric=logic_depth")


def new_run(outdir, label):
    root = Path(outdir or ROOT / "work" / "rtl_agent").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=label + "-", dir=root))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def snapshot(source, extra_sources, run, label):
    paths = [Path(source).resolve(), *(Path(p).resolve() for p in extra_sources)]
    dest = run / label
    dest.mkdir()
    manifest, copies = [], []
    for i, path in enumerate(paths):
        # Includes must be supplied explicitly in M1, so every consumed source is archived.
        data = path.read_bytes()
        if re.search(rb"`include\s", data):
            raise ValueError("M1 does not support `include; provide self-contained sources")
        target = dest / f"{i:03d}_{path.name}"
        target.write_bytes(data)
        copies.append(target)
        manifest.append({"original": str(path), "snapshot": str(target), "sha256": digest(target)})
    save_json(dest / "manifest.json", manifest)
    return copies


def read_sources(paths):
    return "read_verilog -sv " + " ".join(quote(p) for p in paths) + "\n"


def finish(run, result, started):
    import time
    result.runtime_seconds = time.perf_counter() - started
    save_json(run / "result.json", asdict(result))
    return result
