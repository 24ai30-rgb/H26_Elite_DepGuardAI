"""Safe acquisition of a target repository from GitHub into a temporary folder.

Safety rules:
* only https://github.com/<owner>/<repo>[.git]; no credentials, ports, queries, other hosts or protocols
* a shallow, single-branch clone with submodules, hooks/templates, LFS filters and the user's git config disabled
* the clone is only ever read by DepGuard's scanner (file parsing + ast.parse); no repository code is executed here
* size-capped; removal is limited to direct children of the acquisition folder
Needs network access; callers must not call this in offline mode.
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ALLOWED_HOSTS = ("github.com",)
URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")
MAX_MB = 300


def parse_url(url: str) -> tuple[str, str]:
    """-> (owner, repo) or ValueError. Strict on purpose."""
    u = (url or "").strip()
    m = URL_RE.fullmatch(u)
    if not m or m.group(2) in (".", "..") or m.group(2).startswith(".git") or ".." in m.group(2):
        raise ValueError("only plain https://github.com/<owner>/<repo> URLs are accepted (no credentials, ports, queries or other hosts)")
    return m.group(1), m.group(2)


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="https", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def clone_command(url: str, dest: Path) -> list:
    return ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "core.symlinks=false",
            "-c", "core.fsmonitor=false", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false",
            "clone", "--depth", "1", "--single-branch", "--no-tags", "--no-recurse-submodules", "--template=", "--", url, str(dest)]


def _size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6


def _rmtree(path: Path) -> None:
    def fix(func, p, _):  # git object files are read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=fix)


def clone(url: str, root: Path, runner=subprocess.run, timeout=180, max_mb=MAX_MB) -> dict:
    owner, repo = parse_url(url)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix=f"{owner}-{repo}-", dir=root))
    dest = holder / repo
    try:
        r = runner(clone_command(f"https://github.com/{owner}/{repo}.git", dest), capture_output=True, text=True, timeout=timeout,
                   env=_git_env(), stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["no output"]
            raise ValueError(f"git clone failed (exit {r.returncode}): {tail[0][:200]}")
        mb = _size_mb(dest)
        if mb > max_mb:
            raise ValueError(f"repository is {mb:.0f} MB, above the {max_mb} MB limit; not kept")
        return {"path": str(dest), "holder": str(holder), "owner": owner, "repo": repo, "url": f"https://github.com/{owner}/{repo}",
                "size_mb": round(mb, 1), "note": "Shallow clone of the default branch. Scanning parses files only; nothing from the repository is executed."}
    except subprocess.TimeoutExpired:
        _rmtree(holder)
        raise ValueError(f"git clone timed out after {timeout}s; nothing kept")
    except (ValueError, OSError):
        if holder.exists():
            _rmtree(holder)
        raise
    except FileNotFoundError:
        _rmtree(holder)
        raise ValueError("git is not installed or not on PATH")


def list_acquired(root: Path) -> list:
    root = Path(root)
    if not root.is_dir():
        return []
    out = []
    for holder in sorted(p for p in root.iterdir() if p.is_dir()):
        kids = [k for k in holder.iterdir() if k.is_dir()]
        if kids:
            out.append({"holder": str(holder), "path": str(kids[0]), "name": holder.name})
    return out


def remove(path: Path, root: Path) -> None:
    """Delete one acquired clone. `path` must be (inside) a direct child folder of `root`; nothing else is ever deleted."""
    root, p = Path(root).resolve(), Path(path).resolve()
    if not p.is_relative_to(root) or p == root:
        raise ValueError("path is outside the acquisition folder")
    holder = root / p.relative_to(root).parts[0]
    if not holder.is_dir():
        raise ValueError("no such acquired repository")
    _rmtree(holder)
