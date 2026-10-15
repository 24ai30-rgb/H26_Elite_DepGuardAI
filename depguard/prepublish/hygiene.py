"""Repository hygiene: files that should not be tracked. Inspects the tracked file list (git) or the folder; never deletes, rewrites history or force-pushes."""
import fnmatch
import os
import subprocess
from pathlib import Path

BIG_BYTES = 5 * 1024 * 1024
SENSITIVE = ("*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_ed25519", "credentials.json", "secrets.json", "secrets.yml", "secrets.yaml", ".npmrc", ".pypirc", ".netrc")
ENV_OK = (".env.example", ".env.sample", ".env.template", ".env.dist")
JUNK_DIRS = {".venv": "virtual environment", "venv": "virtual environment", "node_modules": "node_modules", "__pycache__": "Python bytecode cache",
             ".pytest_cache": "pytest cache", ".mypy_cache": "mypy cache", ".tox": "tox environments", "build": "build output", "dist": "build output",
             ".idea": "IDE settings", ".ipynb_checkpoints": "notebook checkpoints"}
JUNK_FILES = ("*.pyc", "*.pyo", "*.log", "*.sqlite", "*.sqlite3", ".DS_Store", "Thumbs.db", "*.egg-info")


def tracked_files(root: Path) -> tuple[list, str]:
    """-> (relative paths, source). Uses `git ls-files` when the folder is a git repo, otherwise walks the folder."""
    root = Path(root)
    try:
        r = subprocess.run(["git", "-C", str(root), "-c", "core.fsmonitor=false", "ls-files", "-z"], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout:
            return [f for f in r.stdout.split("\0") if f], "git ls-files"
    except (OSError, subprocess.SubprocessError):
        pass
    out = []   # a bundled .venv / node_modules is reported as ONE entry, not walked (that would take minutes and flood the report)
    for dirpath, dirs, fs in os.walk(root):
        rel = Path(dirpath).relative_to(root).as_posix()
        for d in list(dirs):
            if d == ".git":
                dirs.remove(d)
            elif d in JUNK_DIRS or d.endswith(".egg-info"):
                out.append(f"{rel + '/' if rel != '.' else ''}{d}/.depguard-dir")
                dirs.remove(d)
        out += [f"{rel + '/' if rel != '.' else ''}{f}" for f in sorted(fs)]
    return out, "folder listing (not a git repository)"


def _ignored(rel: str, patterns: list) -> bool:
    parts = rel.split("/")
    for pat in patterns:
        pat = pat.strip("/")
        if fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(x, pat) for x in parts):
            return True
    return False


def check(root: Path, files=None, source=None) -> list:
    root = Path(root)
    if files is None:
        files, source = tracked_files(root)
    gi = root / ".gitignore"
    patterns = [l.strip() for l in gi.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#") and not l.startswith("!")] if gi.is_file() else []
    out = []

    def add(id_, title, conf, detail, files_, rec):
        out.append({"id": id_, "title": title, "status": "warn", "confidence": conf, "detail": detail, "files": files_[:10], "count": len(files_), "recommendation": rec})
    env = [f for f in files if (f.split("/")[-1] == ".env" or f.split("/")[-1].startswith(".env.")) and f.split("/")[-1] not in ENV_OK]
    if env:
        add("env_file_tracked", "Environment file is tracked", "suspected", "These files usually hold secrets. Their contents were not displayed.", env,
            "Stop tracking them (git rm --cached <file>), add them to .gitignore, and rotate any credential they contained. History is not rewritten by DepGuard.")
    sens = [f for f in files if any(fnmatch.fnmatch(f.split("/")[-1], p) for p in SENSITIVE)]
    if sens:
        add("sensitive_file_tracked", "Key or credential file name is tracked", "suspected", "File names suggest private keys or credentials; contents are not shown.", sens,
            "Confirm what they contain; if sensitive, untrack them, rotate the credentials and ignore the pattern.")
    junk = {}
    for f in files:
        parts = f.split("/")
        for d in parts[:-1]:
            if d in JUNK_DIRS or d.endswith(".egg-info"):
                junk.setdefault(JUNK_DIRS.get(d, "build metadata"), []).append(f)
                break
        else:
            if any(fnmatch.fnmatch(parts[-1], p) for p in JUNK_FILES):
                junk.setdefault("generated or local file", []).append(f)
    for kind, fs in junk.items():
        tracked = source == "git ls-files"
        add("generated_files_tracked", f"{kind.capitalize()} is tracked" if tracked else f"{kind.capitalize()} present in the folder", "confirmed" if tracked else "suspected",
            (f"{len(fs)} tracked file(s) look like {kind}." if tracked else f"{len(fs)} file(s) in the folder look like {kind}; this is not a Git repository listing, so whether they are tracked is unknown.")
            + ("" if patterns else " There is no .gitignore."), fs,
            "Untrack them (git rm -r --cached <path>) and add the pattern to .gitignore.")
    big = []
    for f in files:
        try:
            if (root / f).is_file() and (root / f).stat().st_size > BIG_BYTES:
                big.append(f)
        except OSError:
            pass
    if big:
        add("oversized_files", "Large files are tracked", "confirmed", f"Files larger than {BIG_BYTES // 2**20} MB are awkward for ordinary Git.", big,
            "Consider Git LFS or keeping them outside the repository.")
    if not gi.is_file():
        add("no_gitignore", "No .gitignore", "confirmed", "Without one, caches and virtual environments are easy to commit by accident.", [], "Add a .gitignore for Python projects.")
    return out
