"""Controlled experiment: change ONLY the Jinja2 pin (in a temp copy) and compare tests with an untouched baseline.

Usage: python -m depguard.experiments.jinja2_only <repo> <capsule.json> [new_pin]
The capsule is updated in place (field `independent_experiments`, plus a re-rendered capsule.md).
The analyzed repository is never modified; both runs use separate temp copies and venvs.
"""
import json
import sys
from pathlib import Path

from .. import capsule, migrate, verify

# Chosen from package metadata (pip index / resolver report), not guessed: Jinja2 3.1.6 declares Requires-Python >=3.7
# and MarkupSafe>=2.0; LinkWatch only uses Environment, FileSystemLoader and select_autoescape.
DEFAULT_PIN = "Jinja2==3.1.6"
OLD = "Jinja2==2.11.3"


def main(repo: Path, cap_path: Path, pin: str = DEFAULT_PIN) -> dict:
    repo = Path(repo).resolve()
    edits = [{"file": "requirements.txt", "find": OLD, "replace": pin},
             {"file": "pyproject.toml", "find": f'"{OLD}"', "replace": f'"{pin}"'}]
    ok, bad = migrate.validate_edits(repo, edits)
    if bad:
        raise SystemExit(f"edits invalid: {bad}")
    base = verify.verify(repo, None, "exp-baseline")
    new = verify.verify(repo, ok, "exp-jinja2-only")
    exp = {"name": "jinja2-only", "origin": "human-specified experiment, independent of the Gemma recommendation",
           "edits": ok, "diff": migrate.preview(repo, ok), "baseline": base, "proposed": new,
           "comparison": verify.compare(base, new)}
    cap = json.loads(Path(cap_path).read_text(encoding="utf-8"))
    cap["independent_experiments"] = [exp]
    capsule.write(cap, Path(cap_path).parent)
    return exp


if __name__ == "__main__":
    e = main(*sys.argv[1:4])
    print(e["comparison"])
