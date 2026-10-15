"""Deterministic manifest scanner: requirements*.txt and pyproject.toml."""
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


def _dep(req: Requirement, file: str, line: int, kind: str) -> dict:
    pins = [s.version for s in req.specifier if s.operator == "=="]
    return {
        "name": canonicalize_name(req.name),
        "specifier": str(req.specifier),
        "pinned_version": pins[0] if pins else None,
        "marker": str(req.marker) if req.marker else None,
        "kind": kind,
        "file": file,
        "line": line,
    }


def parse_requirements_text(text: str, file: str, kind: str, issues: list) -> list:
    deps = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):  # -r/-c/-e options are not followed
            issues.append({"file": file, "line": n, "issue": f"option line not followed: {line}"})
            continue
        try:
            deps.append(_dep(Requirement(line), file, n, kind))
        except InvalidRequirement as e:
            issues.append({"file": file, "line": n, "issue": f"unparseable requirement: {e}"})
    return deps


def _find_line(text: str, needle: str) -> int:
    for n, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return n
    return 0


# Formats DepGuard recognises but does NOT parse: they are listed so nothing is silently ignored.
UNSUPPORTED = {
    "Pipfile": "Pipenv manifest", "Pipfile.lock": "Pipenv lockfile", "poetry.lock": "Poetry lockfile", "uv.lock": "uv lockfile", "pdm.lock": "PDM lockfile",
    "setup.py": "setuptools script (dependencies are code, not data)", "setup.cfg": "setuptools configuration", "environment.yml": "conda environment",
    "environment.yaml": "conda environment", "package.json": "npm manifest", "package-lock.json": "npm lockfile", "yarn.lock": "yarn lockfile",
    "Cargo.toml": "Rust manifest", "go.mod": "Go module file", "Gemfile": "Ruby manifest", "composer.json": "PHP manifest",
}
LOCKFILES = {"Pipfile.lock", "poetry.lock", "uv.lock", "pdm.lock", "package-lock.json", "yarn.lock"}


def scan(root: Path) -> dict:
    root = Path(root)
    deps, issues, manifests, python_requires = [], [], [], None
    for p in sorted(root.glob("requirements*.txt")):
        kind = "dev" if "dev" in p.name else "runtime"
        manifests.append(p.name)
        try:
            deps += parse_requirements_text(p.read_text(encoding="utf-8"), p.name, kind, issues)
        except (OSError, UnicodeDecodeError) as e:
            issues.append({"file": p.name, "line": 0, "issue": f"unreadable: {e}"})
    pp = root / "pyproject.toml"
    if pp.exists():
        manifests.append(pp.name)
        try:
            text = pp.read_text(encoding="utf-8")
            proj = tomllib.loads(text).get("project", {})
            python_requires = proj.get("requires-python")
            groups = [("runtime", proj.get("dependencies", []))] + [
                (f"extra:{k}", v) for k, v in proj.get("optional-dependencies", {}).items()]
            for kind, items in groups:
                for item in items:
                    try:
                        deps.append(_dep(Requirement(item), pp.name, _find_line(text, item.split()[0][:20]), kind))
                    except InvalidRequirement as e:
                        issues.append({"file": pp.name, "line": 0, "issue": f"unparseable requirement {item!r}: {e}"})
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            issues.append({"file": pp.name, "line": 0, "issue": f"malformed: {e}"})
    unsupported = [{"file": n, "kind": why, "lockfile": n in LOCKFILES} for n, why in UNSUPPORTED.items() if (root / n).is_file()]
    if not manifests:
        issues.append({"file": "", "line": 0, "issue": "no supported manifest found" + (f" (found unsupported: {', '.join(u['file'] for u in unsupported)})" if unsupported else "")})
    return {"manifests": manifests, "python_requires": python_requires, "unsupported_formats": unsupported,
            "lockfiles": [u["file"] for u in unsupported if u["lockfile"]],
            "dependencies": deps, "issues": issues, "conflicts": manifest_conflicts(deps)}


def manifest_conflicts(deps: list) -> list:
    """Same package declared with incompatible constraints in different files."""
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    out, by_name = [], {}
    for d in deps:
        by_name.setdefault(d["name"], []).append(d)
    for name, ds in by_name.items():
        for a in ds:
            for b in ds:
                if a is b or a["file"] == b["file"] or not a["pinned_version"] or not b["specifier"]:
                    continue
                if not SpecifierSet(b["specifier"]).contains(Version(a["pinned_version"]), prereleases=True):
                    out.append({"package": name,
                                "detail": f"{a['file']} pins {a['pinned_version']} but {b['file']} requires '{b['specifier']}'"})
    return out
