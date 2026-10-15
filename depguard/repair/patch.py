"""Patch objects: validation, base-revision tracking, diffs, safe apply, export.

A patch is only ever APPLIED to a designated working copy (never the scanned original), only after the caller holds explicit approval,
and only if the files it touches are byte-identical to what the patch was built against (otherwise: renewed review required).
"""
import ast
import difflib
import hashlib
import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .. import migrate, repo_input

MAX_EDITS = 12
MAX_FILE_BYTES = 1_000_000
MANIFEST_RE = re.compile(r"^(requirements[\w.-]*\.txt|pyproject\.toml|setup\.cfg|tox\.ini)$")
DANGEROUS = re.compile(r"\b(os\.system|subprocess\.|eval\s*\(|exec\s*\(|__import__\s*\(|pickle\.loads|shell\s*=\s*True|socket\.|urllib\.request|requests\.(get|post)\()")
FORBIDDEN_PARTS = {".git", ".depguard-backup", ".venv", "venv", "node_modules", "__pycache__"}


class PatchError(ValueError):
    pass


def _udiff(old: str, new: str, path: str) -> str:
    a, b = old.splitlines(True), new.splitlines(True)
    lines = list(difflib.unified_diff(a, b, f"a/{path}", f"b/{path}"))
    out = []
    for l in lines:   # keep `git apply` happy for files without a trailing newline
        out.append(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n")
    return "".join(out)


def _simulate(root: Path, edits: list):
    """Apply edits sequentially in memory. Returns ({file: (old, new)}, rejected)."""
    state, rejected, ok = {}, [], []   # state[file] = (original LF text, edited LF text, newline style)
    for e in edits:
        rel = e.get("file", "")
        try:
            p = (root / rel).resolve()
            if not p.is_relative_to(root.resolve()) or FORBIDDEN_PARTS & set(Path(rel).parts):
                raise PatchError("path is outside the working copy or in a protected folder")
            if not p.is_file():
                raise PatchError("file does not exist (DepGuard does not create or delete files)")
            if p.stat().st_size > MAX_FILE_BYTES:
                raise PatchError("file is too large to patch")
            old, cur, style = state.get(rel, (None, None, None))
            if old is None:
                old, style = migrate.read_norm(p)
                cur = old
            if not e.get("find") or cur.count(e["find"]) != 1:
                raise PatchError(f"'find' text must occur exactly once (found {cur.count(e['find']) if e.get('find') else 0})")
            if e["find"] == e.get("replace"):
                raise PatchError("edit changes nothing")
            state[rel] = (old, cur.replace(e["find"], e["replace"], 1), style)
            ok.append(e)
        except (PatchError, ValueError, OSError, UnicodeDecodeError, KeyError) as ex:
            rejected.append({"file": rel, "find": (e.get("find") or "")[:120], "reason": str(ex)})
    return state, ok, rejected


def validate_edits(root: Path, edits: list, allowed_sources=()) -> tuple[list, list]:
    """-> (accepted, rejected). Rejects: unknown/unsafe paths, non-unique anchors, files outside the finding's scope, syntax breaks,
    broken manifests, large shrinkage (deletions), and newly introduced dangerous calls in source files."""
    root = Path(root)
    if len(edits) > MAX_EDITS:
        return [], [{"file": "", "find": "", "reason": f"more than {MAX_EDITS} edits in one patch"}]
    allowed = set(allowed_sources)
    scoped, rejected = [], []
    for e in edits:
        rel = (e.get("file") or "").replace("\\", "/")
        e = {**e, "file": rel}
        name = Path(rel).name
        if not (MANIFEST_RE.match(name) and len(Path(rel).parts) == 1) and not (rel in allowed and rel.endswith(".py")):
            rejected.append({"file": rel, "find": (e.get("find") or "")[:120], "reason": "unrelated file: only dependency manifests and source files that use this dependency may change"})
        else:
            scoped.append(e)
    state, ok, bad = _simulate(root, scoped)
    rejected += bad
    final_ok = []
    for e in ok:
        rel = e["file"]
        old, new, _style = state[rel]
        why = None
        if rel.endswith(".py"):
            try:
                ast.parse(new)
            except SyntaxError as ex:
                why = f"edit would break Python syntax: {ex.msg} (line {ex.lineno})"
            added = DANGEROUS.findall(e["replace"]) and not DANGEROUS.search(e["find"])
            if not why and added:
                why = "edit introduces a call that is not a dependency migration (process, network, eval/exec or pickle); not accepted automatically"
        elif rel.endswith(".toml"):
            try:
                tomllib.loads(new)
            except tomllib.TOMLDecodeError as ex:
                why = f"edit would make {rel} invalid TOML: {ex}"
        elif rel.startswith("requirements"):
            from ..scanner import parse_requirements_text
            def bad_count(t):
                iss = []
                parse_requirements_text(t, rel, "runtime", iss)
                return sum("unparseable" in i["issue"] for i in iss)
            if bad_count(new) > bad_count(old):
                why = f"edit would leave an unparseable requirement in {rel}"
        ol, nl = len(old.splitlines()), len(new.splitlines())
        if not why and (not new.strip() or (ol - nl > 5 and nl < ol * 0.5)):
            why = "edit would delete most of the file"
        if why:
            rejected.append({"file": rel, "find": e["find"][:120], "reason": why})
        else:
            final_ok.append(e)
    return final_ok, rejected


def make_patch(root: Path, finding_id: str, package: str, edits: list, origin: str, allowed_sources=(), summary="", rationale="", extras=None, base=None) -> dict:
    """Build a patch object from VALIDATED edits. Raises PatchError when nothing valid remains (the reasons are in the message)."""
    root = Path(root)
    ok, rejected = validate_edits(root, edits, allowed_sources)
    if not ok:
        raise PatchError("no valid edits: " + "; ".join(f"{r['file'] or '(patch)'}: {r['reason']}" for r in rejected[:4]) if rejected else "no edits were proposed")
    state, _, _ = _simulate(root, ok)
    files = []
    for rel in dict.fromkeys(e["file"] for e in ok):
        old, new, _style = state[rel]
        reasons = [e.get("reason", "") for e in ok if e["file"] == rel and e.get("reason")]
        d = _udiff(old, new, rel)
        files.append({"path": rel, "origin": origin, "reason": " / ".join(reasons) or summary, "evidence": [], "verification_status": "not_run", "diff": d,
                      "lines_added": sum(1 for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")),
                      "lines_removed": sum(1 for l in d.splitlines() if l.startswith("-") and not l.startswith("---"))})
    result_hashes = {rel: hashlib.sha256(new.replace("\n", style).encode("utf-8")).hexdigest() for rel, (_old, new, style) in state.items()}   # what the files must contain after applying
    fp = repo_input.tree_fingerprint(root)
    b = {**(base or {}), "fingerprint": fp, "file_hashes": repo_input.file_hashes(root, [f["path"] for f in files])}
    pid = "p-" + hashlib.sha1(json.dumps([finding_id, ok, fp], sort_keys=True).encode()).hexdigest()[:10]
    return {"id": pid, "finding_id": finding_id, "package": package, "origin": origin, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "base": b, "result_hashes": result_hashes, "files": files, "edits": ok, "rejected_edits": rejected, "diff": "".join(f["diff"] for f in files), "summary": summary, "rationale": rationale,
            "status": "proposed", "verification": None, **(extras or {})}


def check_applicable(root: Path, patch: dict) -> dict:
    """Is the working copy still exactly what the patch was built against?"""
    now = repo_input.file_hashes(root, [f["path"] for f in patch["files"]])
    changed = [p for p, h in patch["base"]["file_hashes"].items() if now.get(p) != h]
    if changed:
        return {"ok": False, "stale": True, "changed_files": changed,
                "reason": "these files changed since the patch was created: " + ", ".join(changed) + ". Renewed review is required; the patch will not be applied."}
    ok, bad = validate_edits(root, patch["edits"], {f["path"] for f in patch["files"] if f["path"].endswith(".py")})
    if bad:
        return {"ok": False, "stale": False, "changed_files": [], "reason": "patch no longer applies cleanly: " + "; ".join(b["reason"] for b in bad[:3])}
    return {"ok": True, "stale": False, "changed_files": [], "reason": None}


def apply_patch(workdir: Path, patch: dict) -> dict:
    """Apply to the DESIGNATED working copy only. Snapshot first; verify the real diff equals the reviewed diff."""
    chk = check_applicable(workdir, patch)
    if not chk["ok"]:
        raise PatchError(chk["reason"])
    res = migrate.apply_with_snapshot(workdir, patch["edits"])
    res["patch_id"] = patch["id"]
    return res


def export_unified(patch: dict) -> str:
    """A `git apply`-compatible patch, plus a header recording what it applies to."""
    b = patch["base"]
    head = [f"# DepGuard patch {patch['id']} for finding {patch['finding_id']} ({patch['package']})",
            f"# base commit: {b.get('commit') or 'unknown'}   content fingerprint: {b.get('fingerprint')}",
            f"# origin: {patch['origin']}   verification: {(patch.get('verification') or {}).get('status', 'not_run')}",
            "# Apply with: git apply --check <file>  then  git apply <file>   (review first; nothing here was committed)", ""]
    body = "".join(f"diff --git a/{f['path']} b/{f['path']}\n{f['diff']}" for f in patch["files"])
    return "\n".join(head) + body


# ---------------------------------------------------------------- deterministic generator (no AI): declare an imported-but-undeclared dependency
def declare_dependency(root: Path, package: str, version: str | None = None) -> list:
    """Edits that add `package` to requirements.txt and pyproject.toml [project].dependencies. Deterministic; pins only to a version we actually know."""
    root = Path(root)
    spec = f"{package}=={version}" if version else package
    edits = []
    req = root / "requirements.txt"
    if req.is_file():
        text = req.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        if lines:
            last = lines[-1]
            edits.append({"file": "requirements.txt", "find": last, "replace": f"{last}\n{spec}", "reason": f"declare {package}, imported by the code but missing from this manifest"})
    pp = root / "pyproject.toml"
    if pp.is_file():
        text = pp.read_text(encoding="utf-8")
        m = re.search(r"(?m)^dependencies\s*=\s*\[\s*$", text)
        if m:
            end = text.index("]", m.end())
            items = [l for l in text[m.end():end].splitlines() if l.strip()]
            if items:
                last = items[-1]
                indent = re.match(r"\s*", last).group(0)
                trailing = last.rstrip().endswith(",")
                new_last = last if trailing else last.rstrip() + ","
                edits.append({"file": "pyproject.toml", "find": last, "replace": f'{new_last}\n{indent}"{spec}"{"," if trailing else ""}',
                              "reason": f"declare {package} in [project].dependencies"})
    return edits
