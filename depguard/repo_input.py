"""Repository inputs: ZIP upload, local Git repository, and a working copy for proposed changes.

All three produce a directory DepGuard may read. Nothing here executes repository code.
"""
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from . import walk

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED = 250 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_RATIO = 100                      # uncompressed / compressed, per archive
MANIFESTS = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "poetry.lock", "uv.lock", "environment.yml", "package.json")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", ".pytest_cache", "*.egg-info", ".depguard-backup", "node_modules")


class InputError(ValueError):
    """A repository input was rejected; the message is safe to show to the user."""


def _bad_member(name: str) -> str | None:
    if not name or "\x00" in name:
        return "empty or NUL-containing entry name"
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or n.startswith("//"):
        return "absolute path"
    parts = PurePosixPath(n).parts
    if ".." in parts:
        return "path traversal"
    if any(p.split(".")[0].lower() in WINDOWS_RESERVED for p in parts):
        return "reserved device name"
    if any(c in n for c in '<>:"|?*') and not re.match(r"^[^:]*$", n):
        return "characters not allowed in file names"
    return None


def validate_zip(path: Path) -> dict:
    """Inspect an archive WITHOUT extracting. Raises InputError on anything unsafe. Returns stats."""
    path = Path(path)
    if path.stat().st_size > MAX_ZIP_BYTES:
        raise InputError(f"archive is larger than {MAX_ZIP_BYTES // 2**20} MB")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise InputError("not a valid ZIP archive")
    with zf:
        infos = zf.infolist()
        if not infos:
            raise InputError("archive is empty")
        if len(infos) > MAX_ENTRIES:
            raise InputError(f"archive has more than {MAX_ENTRIES} entries")
        total = 0
        for i in infos:
            why = _bad_member(i.filename)
            if why:
                raise InputError(f"unsafe entry {i.filename!r}: {why}")
            mode = i.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise InputError(f"unsafe entry {i.filename!r}: symbolic link")
            if i.flag_bits & 0x1:
                raise InputError("encrypted archives are not supported")
            total += i.file_size
            if total > MAX_UNCOMPRESSED:
                raise InputError(f"archive would extract to more than {MAX_UNCOMPRESSED // 2**20} MB")
        packed = max(1, sum(i.compress_size for i in infos))
        if total / packed > MAX_RATIO and total > 5 * 2**20:
            raise InputError("suspicious compression ratio (possible zip bomb)")
        names = [i.filename.replace("\\", "/") for i in infos if not i.is_dir()]
    return {"entries": len(infos), "uncompressed_bytes": total, "names": names}


def _project_root(extracted: Path) -> Path:
    """A ZIP of a repo usually has one top-level folder (GitHub zipballs always do)."""
    kids = [k for k in extracted.iterdir() if k.name != "__MACOSX"]
    return kids[0] if len(kids) == 1 and kids[0].is_dir() else extracted


def extract_zip(archive: Path, workspace_root: Path) -> dict:
    """Validate, then extract into a fresh folder under `workspace_root`. Returns {path, files, manifests, has_git, notes}."""
    archive = Path(archive)
    stats = validate_zip(archive)
    workspace_root = Path(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix="zip-", dir=workspace_root))
    dest = holder / "extracted"
    try:
        dest.mkdir()
        base = dest.resolve()
        with zipfile.ZipFile(archive) as zf:
            for i in zf.infolist():
                target = (dest / i.filename.replace("\\", "/")).resolve()
                if not target.is_relative_to(base):  # defence in depth after validate_zip
                    raise InputError(f"unsafe entry {i.filename!r}: escapes the workspace")
                if i.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        root = _project_root(dest)
        found = sorted(m for m in MANIFESTS if (root / m).exists()) + sorted(p.name for p in root.glob("requirements*.txt") if p.name != "requirements.txt")
        has_py = any(True for _ in walk.iter_files(root, suffixes=(".py",)))
        if not found and not has_py:
            raise InputError("the archive does not look like a usable project: no dependency manifest and no Python files were found")
        return {"path": str(root), "holder": str(holder), "files": stats["entries"], "manifests": found, "has_git": (root / ".git").exists(),
                "notes": ["Extracted into an isolated temporary workspace; nothing was executed.",
                          "A ZIP may not contain Git history; revision tracking uses a content hash instead of a commit SHA." if not (root / ".git").exists() else "A .git folder is present."]}
    except Exception:
        remove_workspace(holder, workspace_root)
        raise


def remove_workspace(path: Path, root: Path) -> None:
    """Delete one workspace folder; refuses anything that is not a direct child of `root`."""
    root, p = Path(root).resolve(), Path(path).resolve()
    if not p.is_relative_to(root) or p == root:
        raise InputError("path is outside the workspace folder")
    holder = root / p.relative_to(root).parts[0]

    def fix(func, pth, _):
        os.chmod(pth, stat.S_IWRITE)
        func(pth)
    if holder.is_dir():
        shutil.rmtree(holder, onerror=fix)


# ---------------------------------------------------------------- local Git
def _git(root: Path, *args, timeout=30) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "-c", "core.fsmonitor=false", *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.stdout.rstrip("\n") if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_info(root: Path) -> dict:
    """Branch, commit and working-tree state. Read-only. `is_git` False for non-repositories (e.g. ZIP extracts)."""
    root = Path(root)
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return {"is_git": False, "branch": None, "commit": None, "dirty": None, "modified": [], "untracked": [], "remote": None}
    status = _git(root, "status", "--porcelain=v1", "-uall") or ""
    modified, untracked = [], []
    for line in status.splitlines():
        (untracked if line.startswith("??") else modified).append(line[3:].strip().strip('"'))
    remote = _git(root, "remote", "get-url", "origin")
    return {"is_git": True, "toplevel": top, "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"), "commit": _git(root, "rev-parse", "HEAD"),
            "dirty": bool(modified or untracked), "modified": modified, "untracked": untracked,
            "remote": re.sub(r"://[^/@]+@", "://", remote) if remote else None}


def file_hashes(root: Path, files) -> dict:
    out = {}
    for f in files:
        p = Path(root) / f
        out[f] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return out


def tree_fingerprint(root: Path) -> str:
    """Content hash of dependency manifests + Python sources: the 'revision' for inputs without a commit (ZIP, dirty tree)."""
    h = hashlib.sha256()
    root = Path(root)
    for p in walk.iter_files(root):
        rel = p.relative_to(root)
        if not p.is_file() or {".git", ".venv", "venv", "__pycache__", ".depguard-backup"} & set(rel.parts):
            continue
        if p.suffix in (".py", ".toml", ".txt", ".cfg", ".lock") or p.name in MANIFESTS:
            h.update(rel.as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def make_working_copy(src: Path, dest_root: Path) -> dict:
    """Copy the CURRENT working tree (including the user's uncommitted changes) so proposals apply to what the user actually has.
    The original is never modified; `.git` is not copied, so the copy cannot be committed to by accident."""
    src = Path(src).resolve()
    info = git_info(src)
    dest_root = Path(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix=f"{src.name}-", dir=dest_root))
    dest = holder / src.name
    dest_r = dest.resolve()

    def ignore(dirpath, names):
        """The usual junk, plus any directory that CONTAINS the destination (the data folder often lives inside the repository being copied;
        descending into it would recurse forever)."""
        skip = set(COPY_IGNORE(dirpath, names))
        for n in names:
            p = (Path(dirpath) / n).resolve()
            if p == dest_r or dest_r.is_relative_to(p):
                skip.add(n)
        return skip
    shutil.copytree(src, dest, ignore=ignore)
    return {"path": str(dest), "holder": str(holder), "base": {"commit": info["commit"], "branch": info["branch"], "dirty": info["dirty"],
            "modified_files": info["modified"], "untracked_files": info["untracked"], "fingerprint": tree_fingerprint(src)},
            "note": "Copy of the working tree as it is now, including uncommitted changes; the original is untouched."}
