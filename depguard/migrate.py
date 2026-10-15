"""Safe edit proposals: validate against a root, preview as diffs, apply only on approval."""
import difflib
import tomllib
from pathlib import Path


def read_norm(path) -> tuple[str, str]:
    """-> (text with LF newlines, original newline style). Refuses mixed endings: normalising them would rewrite unrelated lines."""
    b = Path(path).read_bytes()
    crlf, lf_only = b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")
    if crlf and lf_only:
        raise ValueError("file has mixed line endings; DepGuard will not edit it (it would change unrelated lines)")
    return b.decode("utf-8").replace("\r\n", "\n"), ("\r\n" if crlf else "\n")


def write_norm(path, text: str, style: str) -> None:
    Path(path).write_bytes(text.replace("\n", style).encode("utf-8"))


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
            n = read_norm(p)[0].count(e["find"])
            if not e["find"] or n != 1:
                raise ValueError(f"'find' text must occur exactly once (found {n})")
            _check_manifest_still_parses(p, read_norm(p)[0], e)
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
    chunks, new_text = [], {}
    for e in edits:  # cumulative per file, in first-appearance order: matches what apply_edits really produces
        f = e["file"]
        new_text[f] = new_text.get(f, read_norm(root / f)[0]).replace(e["find"], e["replace"], 1)
    for f, new in new_text.items():
        old = read_norm(root / f)[0]
        chunks += difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{f}", f"b/{f}")
    return "".join(chunks)


def apply_edits(workdir: Path, edits: list) -> None:
    """Write edits into `workdir`. Caller is responsible for obtaining human approval first."""
    ok, bad = validate_edits(workdir, edits)
    if bad:
        raise ValueError(f"refusing to apply, invalid edits: {bad}")
    for e in ok:
        p = Path(workdir) / e["file"]
        text, style = read_norm(p)
        write_norm(p, text.replace(e["find"], e["replace"], 1), style)   # keeps the file's own line endings byte-for-byte


BACKUP_DIR = ".depguard-backup"


def _diff_files(old_dir: Path, new_dir: Path, files) -> str:
    chunks = []
    for f in files:
        a = (old_dir / f).read_text(encoding="utf-8")
        b = (new_dir / f).read_text(encoding="utf-8")
        chunks += difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{f}", f"b/{f}")
    return "".join(chunks)


def apply_with_snapshot(workdir: Path, edits: list) -> dict:
    """Snapshot -> apply -> verify the real diff equals the previewed diff. Caller must already hold human approval."""
    from datetime import datetime
    workdir = Path(workdir).resolve()
    ok, bad = validate_edits(workdir, edits)
    if bad or not ok:
        raise ValueError(f"edits do not apply cleanly to {workdir}: {bad or 'no edits'}")
    expected = preview(workdir, ok)
    files = list(dict.fromkeys(e["file"] for e in ok))  # first-appearance order, same as preview()
    snap = workdir / BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for f in files:
        (snap / f).parent.mkdir(parents=True, exist_ok=True)
        (snap / f).write_bytes((workdir / f).read_bytes())
    apply_edits(workdir, ok)
    actual = _diff_files(snap, workdir, files)
    return {"applied": files, "backup": str(snap), "diff": actual, "diff_matches_preview": actual == expected,
            "rollback": [f"restore from the dashboard (Rollback) or the API using backup {snap}",
                         f"or copy the files in {snap} back over the working copy",
                         "or, if the working copy is a git repo: git checkout -- " + " ".join(sorted(files))]}


def rollback(workdir: Path, snapshot: Path) -> dict:
    """Restore every file stored in a snapshot that lives under <workdir>/.depguard-backup/."""
    workdir, snapshot = Path(workdir).resolve(), Path(snapshot).resolve()
    if not snapshot.is_relative_to((workdir / BACKUP_DIR).resolve()) or not snapshot.is_dir():
        raise ValueError("snapshot must be a directory inside the working copy's .depguard-backup/")
    restored = []
    for src in sorted(p for p in snapshot.rglob("*") if p.is_file()):
        rel = src.relative_to(snapshot)
        dest = (workdir / rel).resolve()
        if not dest.is_relative_to(workdir):
            raise ValueError("snapshot entry escapes the working copy")
        dest.write_bytes(src.read_bytes())
        restored.append(rel.as_posix())
    if not restored:
        raise ValueError("snapshot is empty")
    return {"restored": restored, "diff_vs_snapshot": _diff_files(snapshot, workdir, restored)}
