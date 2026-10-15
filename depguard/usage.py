"""AST-based import mapping. Static only: dynamic imports are reported, not resolved."""
import ast
import sys
from pathlib import Path

from . import walk

from packaging.utils import canonicalize_name

SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "__pycache__", "depguard", "node_modules"}
# import name -> distribution name, where they differ (static table; extend as needed)
ALIASES = {"dateutil": "python-dateutil", "pkg_resources": "setuptools", "yaml": "pyyaml",
           "bs4": "beautifulsoup4", "PIL": "pillow", "cv2": "opencv-python", "sklearn": "scikit-learn"}


def _py_files(root: Path):
    yield from walk.iter_files(root, SKIP_DIRS, (".py",))


def map_usage(root: Path, declared: set) -> dict:
    root = Path(root)
    local = {p.name.removesuffix(".py") for p in root.iterdir() if p.suffix == ".py" or p.is_dir()}
    usages, dynamic, private, errors = {}, [], [], []
    for path in _py_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError, OSError) as e:
            errors.append({"file": rel, "error": str(e)})
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [(a.name, None) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [(node.module, [a.name for a in node.names])]
            elif isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                if name in ("__import__", "import_module"):
                    dynamic.append({"file": rel, "line": node.lineno})
            for mod, names in mods:
                top = mod.split(".")[0]
                if top in sys.stdlib_module_names or top in local:
                    continue
                dist = canonicalize_name(ALIASES.get(top, top))
                usages.setdefault(dist, []).append(
                    {"file": rel, "line": node.lineno, "module": mod, "names": names or []})
                for n in names or []:
                    if n.startswith("_"):
                        private.append({"package": dist, "file": rel, "line": node.lineno, "name": f"{mod}.{n}"})
    return {
        "usages": usages,
        "undeclared_imports": sorted(set(usages) - declared),
        "no_import_found": sorted(declared - set(usages)),
        "private_api_imports": private,
        "dynamic_import_sites": dynamic,
        "parse_errors": errors,
        "limitations": [
            "Static AST analysis only; imports inside strings, plugins, entry points or dynamic import calls are not resolved.",
            "'no_import_found' means no static import was seen; it does NOT prove the package is unused (transitive, runtime or tooling use is possible).",
            "Import-name to distribution mapping uses a small static alias table; unusual mappings may be misreported as undeclared.",
        ],
    }
