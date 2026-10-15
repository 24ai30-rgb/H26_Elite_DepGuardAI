"""Safe edit proposals: validate against a root, preview as diffs, apply only on approval."""
import difflib
import tomllib
from pathlib import Path


def validate_edits(root: Path, edits: list) -> tuple[list, list]:
    """Return (accepted, rejected). Edits are {file, find, replace}; `find` must occur exactly once."""
    root = Path(root).resolve()
    ok, bad = [], []
    for e in edits:
        try:
            p = (root / e["file"]).resolve()
            if not p.is_relative_to(root):
                raise ValueError("path escapes working copy")
            if not p.is_file():
                raise ValueError("file does not exist")
            n = p.read_text(encoding="utf-8").count(e["find"])
            if not e["find"] or n != 1:
                raise ValueError(f"'find' text must occur exactly once (found {n})")
            _check_manifest_still_parses(p, p.read_text(encoding="utf-8"), e)
            ok.append(e)
        except (KeyError, ValueError, OSError) as ex:
            bad.append({**{k: e.get(k) for k in ("file", "find")}, "reason": str(ex)})
    return ok, bad


def _check_manifest_still_parses(path: Path, old_text: str, e: dict) -> None:
    """Reject edits that make a manifest worse (pre-existing problems are not blamed on the edit)."""
    new_text = old_text.replace(e["find"], e["replace"], 1)
    if path.suffix == ".toml":
        try:
            tomllib.loads(new_text)
        except tomllib.TOMLDecodeError as ex:
            raise ValueError(f"edit would make {path.name} invalid TOML: {ex}")
    elif path.name.startswith("requirements") and path.suffix == ".txt":
        from .scanner import parse_requirements_text
        def bad_count(text):
            issues = []
            parse_requirements_text(text, path.name, "runtime", issues)
            return sum("unparseable" in i["issue"] for i in issues)
        if bad_count(new_text) > bad_count(old_text):
            raise ValueError(f"edit would leave an unparseable requirement in {path.name}")


def preview(root: Path, edits: list) -> str:
    root = Path(root)
    chunks = []
    for e in edits:
        old = (root / e["file"]).read_text(encoding="utf-8")
        new = old.replace(e["find"], e["replace"], 1)
        chunks += difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                       f"a/{e['file']}", f"b/{e['file']}")
    return "".join(chunks)


def apply_edits(workdir: Path, edits: list) -> None:
    """Write edits into `workdir`. Caller is responsible for obtaining human approval first."""
    ok, bad = validate_edits(workdir, edits)
    if bad:
        raise ValueError(f"refusing to apply, invalid edits: {bad}")
    for e in ok:
        p = Path(workdir) / e["file"]
        p.write_text(p.read_text(encoding="utf-8").replace(e["find"], e["replace"], 1), encoding="utf-8")
