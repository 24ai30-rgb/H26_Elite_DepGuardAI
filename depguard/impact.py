"""Dependency -> module -> test impact graph, built statically with `ast`. Evidence only; uncertainty is reported, never hidden.

For each third-party package: the files that import it (file:line, names, alias, optional/try-except), the repository modules that
import those files (transitively), the tests that import the package or any of those modules, and plain-text mentions in
non-Python config files. Dynamic imports, star imports, aliases and optional imports are listed as reasons the graph may be incomplete.
"""
import ast
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

from packaging.utils import canonicalize_name

from . import walk
from .usage import ALIASES, SKIP_DIRS

CONFIG_FILES = ("Dockerfile", "Makefile", "tox.ini", "setup.cfg", "pytest.ini", ".pre-commit-config.yaml", ".github", "pyproject.toml")


def _is_test(rel: str) -> bool:
    p = Path(rel)
    return "tests" in p.parts or "test" in p.parts or p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py"


def _module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _try_except_import_lines(tree) -> set:
    """Lines of imports guarded by `try: ... except ImportError/ModuleNotFoundError`."""
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            names = {getattr(h.type, "id", None) or getattr(getattr(h.type, "attr", None), "__str__", lambda: None)() for h in node.handlers if h.type is not None}
            tuples = [n for h in node.handlers if isinstance(h.type, ast.Tuple) for n in h.type.elts]
            names |= {getattr(n, "id", None) for n in tuples}
            if names & {"ImportError", "ModuleNotFoundError", "Exception"} or any(h.type is None for h in node.handlers):
                for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        lines.add(sub.lineno)
    return lines


def build(root: Path, declared: set | None = None) -> dict:
    root = Path(root)
    files = {}
    for p in walk.iter_files(root, SKIP_DIRS, (".py",)):
        rel = p.relative_to(root).as_posix()
        if set(Path(rel).parts[:-1]) & SKIP_DIRS:
            continue
        files[rel] = p
    mod_to_file = {_module_name(r): r for r in files}
    packages = defaultdict(list)           # third-party dist -> import records
    edges = defaultdict(set)               # file -> files it imports (repository-internal)
    dynamic, stars, errors = [], [], []
    for rel, p in files.items():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError, OSError) as e:
            errors.append({"file": rel, "error": str(e)[:120]})
            continue
        optional = _try_except_import_lines(tree)
        pkg_parts = Path(rel).parent.parts
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                nm = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                if nm in ("__import__", "import_module"):
                    dynamic.append({"file": rel, "line": node.lineno})
            targets = []   # (module, names, alias, level)
            if isinstance(node, ast.Import):
                targets = [(a.name, [], a.asname, 0) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [(node.module or "", [a.name for a in node.names], None, node.level)]
                if any(a.name == "*" for a in node.names):
                    stars.append({"file": rel, "line": node.lineno, "module": node.module})
            for mod, names, alias, level in targets:
                if level:   # relative import inside the repository
                    base = list(pkg_parts[: len(pkg_parts) - (level - 1)]) if level > 1 else list(pkg_parts)
                    cand = ".".join(base + ([mod] if mod else []))
                    hits = [mod_to_file[f"{cand}.{n}"] for n in names if f"{cand}.{n}" in mod_to_file]
                    if cand in mod_to_file and cand != _module_name(rel):
                        hits.append(mod_to_file[cand])
                    edges[rel].update(h for h in hits if h != rel)
                    continue
                top = mod.split(".")[0]
                local = mod_to_file.get(mod) or mod_to_file.get(top)
                nested = [mod_to_file[f"{mod}.{n}"] for n in names if f"{mod}.{n}" in mod_to_file]
                if local or nested:
                    edges[rel].update(([local] if local else []) + nested)
                    continue
                if top in sys.stdlib_module_names or not top:
                    continue
                dist = canonicalize_name(ALIASES.get(top, top))
                packages[dist].append({"file": rel, "line": node.lineno, "module": mod, "names": names, "alias": alias, "optional": node.lineno in optional})
    reverse = defaultdict(set)
    for src, dsts in edges.items():
        for d in dsts:
            reverse[d].add(src)

    def importers(start_files):
        """Files that (transitively) import any of `start_files`, with the chain to the first hop."""
        seen = {f: [f] for f in start_files}
        q = deque(start_files)
        while q:
            cur = q.popleft()
            for nxt in reverse.get(cur, ()):
                if nxt not in seen:
                    seen[nxt] = seen[cur] + [nxt]
                    q.append(nxt)
        return {f: chain for f, chain in seen.items() if f not in start_files}

    texts = {}
    for name in CONFIG_FILES:
        t = root / name
        for f in ([t] if t.is_file() else [x for x in t.rglob("*") if x.is_file()] if t.is_dir() else []):
            try:
                texts[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                pass

    out = {}
    for pkg in sorted(set(packages) | set(declared or ())):
        recs = packages.get(pkg, [])
        direct_files = sorted({r["file"] for r in recs})
        dep = importers(set(direct_files))
        tests_direct = sorted(f for f in direct_files if _is_test(f))
        tests_indirect = [{"file": f, "via": c} for f, c in sorted(dep.items()) if _is_test(f)]
        mentions = []
        for fname, lines in texts.items():
            if fname in ("pyproject.toml",) or Path(fname).name.startswith("requirements"):
                continue
            for i, line in enumerate(lines, 1):
                if re.search(rf"(?<![\w-]){re.escape(pkg)}(?![\w-])", line, re.I):
                    mentions.append({"file": fname, "line": i, "text": line.strip()[:120]})
        why = []
        if any(r["optional"] for r in recs):
            why.append("some imports sit inside try/except ImportError (optional use)")
        if any(r["alias"] for r in recs):
            why.append("imported under an alias; call sites use the alias name")
        if dynamic:
            why.append(f"{len(dynamic)} dynamic import site(s) in the repository could use this package invisibly")
        if any(s["module"] and s["module"].split(".")[0] == pkg for s in stars):
            why.append("star import from this package hides which names are used")
        if not recs:
            why.append("no static import found; this is NOT proof the package is unused (transitive, plugin, tooling or dynamic use is invisible here)")
        why.append("reflection, getattr-based access and indirect use through other libraries are not tracked")
        out[pkg] = {"direct": recs, "direct_files": direct_files, "dependent_files": sorted(f for f in dep if not _is_test(f)),
                    "tests_direct": tests_direct, "tests_indirect": tests_indirect, "config_mentions": mentions[:10],
                    "affected_files": sorted(set(direct_files) | set(dep)), "uncertainty": why,
                    "summary": f"{len(recs)} import site(s) in {len(direct_files)} file(s); {len(dep)} more file(s) import those modules; "
                               f"{len(tests_direct) + len(tests_indirect)} test file(s) reach it ({len(tests_direct)} directly)."}
    return {"packages": out, "dynamic_import_sites": dynamic, "star_imports": stars, "parse_errors": errors,
            "limitations": ["Static analysis only: dynamic imports, reflection, plugin entry points and runtime configuration are not resolved.",
                            "Test reachability follows import edges between repository files; a test that exercises code without importing it (subprocess, CLI) is not detected."]}
