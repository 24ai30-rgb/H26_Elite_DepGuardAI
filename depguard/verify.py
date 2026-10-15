"""Run tests for an (optionally edited) copy of the repo inside a throwaway venv."""
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import migrate

IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", "depguard", ".pytest_cache", "*.egg-info")
STATES = ("passed", "failed", "skipped", "not_run", "environment_blocked")


def run(cmd, cwd, timeout):
    """Execute a command; record argv, exit code, wall-clock duration and output (tail-truncated). Only a wall-clock timeout is enforced."""
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out, code = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out, code = f"timeout after {timeout}s\n{(e.stdout or b'')[-2000:]}", -1
    except OSError as e:
        out, code = str(e), -2
    return {"command": [str(c) for c in cmd], "exit_code": code, "duration_s": round(time.monotonic() - t0, 2), "timeout_s": timeout, "output": out[-12000:]}


def verify(root: Path, edits: list | None, label: str, progress=None, **kw) -> dict:
    """Run `_verify` and scrub local paths (temp dir, interpreter, home) from the recorded result."""
    res = _verify(root, edits, label, progress=progress, **kw)
    raw = json.dumps(res)
    wd = res.get("workdir")
    for path, repl in ((wd, "<tmp>"), (wd and Path(wd).as_posix(), "<tmp>"), (sys.executable, "<python>"),
                       (str(Path.home()), "<home>"), (Path.home().as_posix(), "<home>")):
        if path:
            raw = raw.replace(json.dumps(path)[1:-1], repl)
    return json.loads(raw)


def _environment() -> dict:
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(), "isolation": "venv inside a temporary copy of the repo; NOT a container or sandbox",
            "network_needed": "pip install fetches packages from the configured package index"}


def _failure_kind(step_name: str, code: int) -> str:
    if code == -1:
        return "timeout"
    if step_name == "venv":
        return "venv_failure"
    if step_name == "install":
        return "install_failure"
    return "test_failures" if code == 1 else "pytest_collection_or_usage_error" if 2 <= code <= 5 else "environment_error"


def _verify(root: Path, edits: list | None, label: str, install_timeout=900, test_timeout=600, progress=None) -> dict:
    """Copy `root` to a temp dir, apply edits there, install requirements*.txt, run pytest.

    Isolation is a separate venv + copied tree (NOT a container/sandbox): setup and test code of
    the target still runs as the current user.
    """
    if sys.version_info[:2] not in ((3, 12), (3, 13)):
        return {"label": label, "status": "environment_blocked", "failure_kind": "environment_error", "environment": _environment(),
                "reason": f"Python {sys.version_info[:2]} unsupported (need 3.12/3.13)", "steps": []}
    tmp = Path(tempfile.mkdtemp(prefix=f"depguard-{label}-"))
    work, steps = tmp / "repo", []
    shutil.copytree(root, work, ignore=IGNORE)
    res = {"label": label, "workdir": str(tmp), "steps": steps, "status": "not_run", "tests": {}, "failure_kind": None,
           "environment": _environment()}
    if edits:
        migrate.apply_edits(work, edits)  # applied to the temp copy only
    say = progress or (lambda m: None)
    say(f"[{label}] creating an isolated virtual environment")
    steps.append(run([sys.executable, "-m", "venv", tmp / "venv"], tmp, 300))
    if steps[-1]["exit_code"] != 0:
        res.update(status="environment_blocked", failure_kind=_failure_kind("venv", steps[-1]["exit_code"]))
        return res
    py = tmp / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    reqs = [f.name for f in sorted(work.glob("requirements*.txt"))]
    if steps[-1]["exit_code"] == 0 and reqs:
        say(f"[{label}] installing requirements (needs the package index)")
        cmd = [py, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
        for r in reqs:
            cmd += ["-r", r]
        steps.append(run(cmd, work, install_timeout))
    if steps[-1]["exit_code"] != 0:
        res.update(status="environment_blocked", failure_kind=_failure_kind("install", steps[-1]["exit_code"]))
        return res
    say(f"[{label}] checking the installed packages")
    steps.append(run([py, "-m", "pip", "--version"], work, 60))
    res["environment"]["venv_pip"] = steps[-1]["output"].strip().split(" from ")[0]
    steps.append(run([py, "-m", "pip", "check"], work, 120))
    res["pip_check"] = steps[-1]
    res["resolved"] = _resolved(py, work)
    say(f"[{label}] running the project's tests")
    junit = tmp / "junit.xml"
    steps.append(run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"], work, test_timeout))
    t = steps[-1]
    res.update(output=t["output"], exit_code=t["exit_code"])
    res["tests"] = _junit(junit)
    # pytest exit codes 1-5 mean it ran (failures, collection/usage errors); anything else is an environment problem
    res["status"] = "passed" if t["exit_code"] == 0 else "failed" if 0 < t["exit_code"] <= 5 else "environment_blocked"
    res["failure_kind"] = None if t["exit_code"] == 0 else _failure_kind("pytest", t["exit_code"])
    return res


def _resolved(py, cwd) -> dict:
    r = run([py, "-m", "pip", "list", "--format=json"], cwd, 120)
    try:
        return {p["name"].lower().replace("_", "-"): p["version"] for p in json.loads(r["output"][r["output"].index("["):])}
    except ValueError:
        return {}


def _junit(path: Path) -> dict:
    try:
        cases = ET.parse(path).getroot().iter("testcase")
    except (OSError, ET.ParseError):
        return {}
    out = {}
    for c in cases:
        name = f"{c.get('classname')}::{c.get('name')}"
        out[name] = ("failed" if c.find("failure") is not None or c.find("error") is not None
                     else "skipped" if c.find("skipped") is not None else "passed")
    return out


def compare(base: dict, new: dict) -> dict:
    bt, nt = base.get("tests", {}), new.get("tests", {})
    return {"baseline_status": base["status"], "proposed_status": new["status"],
            "note": None if nt else "proposed run produced no per-test results (e.g. collection/import error): 'regressed' lists every baseline-passing test",
            "fixed": sorted(k for k in bt if bt[k] == "failed" and nt.get(k) == "passed"),
            "regressed": sorted(k for k in bt if bt[k] == "passed" and nt.get(k) != "passed"),
            "baseline_counts": _count(bt), "proposed_counts": _count(nt)}


def _count(t: dict) -> dict:
    return {s: sum(v == s for v in t.values()) for s in ("passed", "failed", "skipped")}
