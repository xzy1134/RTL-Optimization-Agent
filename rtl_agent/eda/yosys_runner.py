"""Run Yosys without a shell; kill Yosys and ABC descendants on timeout."""
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess


@dataclass
class RunResult:
    returncode: int | None
    timed_out: bool
    log: str
    error: str = ""


def run_yosys(config, script, run_dir, name="yosys", timeout=None):
    run_dir = Path(run_dir)
    script_path = run_dir / f"{name}.ys"
    log_path = run_dir / f"{name}.log"
    script_path.write_text(script)
    limit = config.timeout_seconds if timeout is None else timeout
    with log_path.open("w") as log:
        if limit <= 0:
            log.write("Time budget exhausted before tool launch.\n")
            return RunResult(None, True, str(log_path))
        try:
            process = subprocess.Popen(
                [config.yosys_bin, "-T", "-s", str(script_path)],
                cwd=run_dir, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except OSError as exc:
            log.write(str(exc) + "\n")
            return RunResult(None, False, str(log_path), str(exc))
        try:
            return RunResult(process.wait(timeout=limit), False, str(log_path))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            log.write(f"\nPython time budget exceeded ({limit:.6f}s).\n")
            return RunResult(process.returncode, True, str(log_path))


def yosys_version(config):
    try:
        p = subprocess.run([config.yosys_bin, "-V"], capture_output=True, text=True,
                           timeout=min(config.timeout_seconds, 5))
        if p.returncode:
            raise ValueError(p.stderr.strip() or "Yosys version check failed")
        return p.stdout.strip()
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Yosys version check timed out") from exc
    except OSError as exc:
        raise ValueError(f"Cannot obtain Yosys version: {exc}") from exc
