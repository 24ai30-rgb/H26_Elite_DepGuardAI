"""One pruned directory walker shared by every scan. `Path.rglob` descends into .venv/node_modules before filtering; this does not."""
import os
from pathlib import Path

SKIP_DIRS = frozenset({".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".depguard-backup", ".pytest_cache", ".mypy_cache", ".tox", "build", "dist",
                       ".idea", ".ipynb_checkpoints"})
TOOL_DIRS = SKIP_DIRS | {"depguard"}   # scans of a target repo that contains DepGuard itself ignore DepGuard's own folder


def iter_files(root: Path, skip=SKIP_DIRS, suffixes=None):
    """Yield files under root in a deterministic order, never entering skipped directories (or *.egg-info)."""
    root = Path(root)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in skip and not d.endswith(".egg-info"))
        for f in sorted(files):
            if suffixes is None or f.endswith(suffixes):
                yield Path(dirpath) / f
