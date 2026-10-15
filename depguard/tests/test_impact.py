"""Impact graph on a small fixture repo with known structure."""
from pathlib import Path

import pytest

from depguard import impact


@pytest.fixture
def repo(tmp_path):
    files = {
        "app/__init__.py": "",
        "app/core.py": "import requests\nfrom dateutil import parser\n",
        "app/cli.py": "from . import core\nimport click\n",
        "app/optional.py": "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n",
        "app/dyn.py": "import importlib\nm = importlib.import_module('plugin')\n",
        "app/star.py": "from toml import *\n",
        "app/aliased.py": "import numpy as np\n",
        "tests/__init__.py": "",
        "tests/test_cli.py": "from app import cli\n",
        "tests/test_core.py": "import app.core\n",
        "tests/test_pure.py": "import os\n",
        "Dockerfile": "RUN pip install requests\n",
        "Makefile": "test:\n\tpytest\n",
        "requirements.txt": "requests\nnever-imported\n",
    }
    for n, t in files.items():
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t)
    return tmp_path


def test_direct_users_with_lines_and_alias_mapping(repo):
    g = impact.build(repo, {"requests", "python-dateutil", "never-imported"})["packages"]
    assert [(r["file"], r["line"]) for r in g["requests"]["direct"]] == [("app/core.py", 1)]
    assert g["python-dateutil"]["direct"][0]["module"] == "dateutil"      # import name mapped to the distribution
    assert g["requests"]["direct_files"] == ["app/core.py"]


def test_dependents_and_tests_are_found_through_import_edges(repo):
    r = impact.build(repo)["packages"]["requests"]
    assert r["dependent_files"] == ["app/cli.py"]                          # cli imports core via a relative import
    assert r["tests_direct"] == []
    via = {t["file"]: t["via"] for t in r["tests_indirect"]}
    assert set(via) == {"tests/test_cli.py", "tests/test_core.py"}         # test_pure.py is unrelated
    assert via["tests/test_cli.py"] == ["app/core.py", "app/cli.py", "tests/test_cli.py"]
    assert "app/cli.py" in r["affected_files"] and "tests/test_pure.py" not in r["affected_files"]


def test_declared_but_unimported_package_is_uncertain_not_unused(repo):
    r = impact.build(repo, {"never-imported"})["packages"]["never-imported"]
    assert r["direct"] == [] and r["affected_files"] == []
    assert any("NOT proof the package is unused" in u for u in r["uncertainty"])


def test_uncertainty_sources_are_reported(repo):
    g = impact.build(repo, {"pyyaml", "numpy", "toml", "requests"})
    p = g["packages"]
    assert p["pyyaml"]["direct"][0]["optional"] is True and any("try/except ImportError" in u for u in p["pyyaml"]["uncertainty"])
    assert p["numpy"]["direct"][0]["alias"] == "np" and any("alias" in u for u in p["numpy"]["uncertainty"])
    assert any("star import" in u for u in p["toml"]["uncertainty"])
    assert g["dynamic_import_sites"] == [{"file": "app/dyn.py", "line": 2}]
    assert any("dynamic import" in u for u in p["requests"]["uncertainty"])
    assert any("reflection" in u for u in p["requests"]["uncertainty"])


def test_config_mentions_are_textual_hints_only(repo):
    m = impact.build(repo)["packages"]["requests"]["config_mentions"]
    assert m == [{"file": "Dockerfile", "line": 1, "text": "RUN pip install requests"}]   # manifests are excluded; Makefile has no mention


def test_test_file_importing_the_package_directly_is_a_direct_test(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import requests\n")
    r = impact.build(tmp_path)["packages"]["requests"]
    assert r["tests_direct"] == ["tests/test_x.py"] and "1 directly" in r["summary"]


def test_syntax_errors_are_reported_not_fatal(tmp_path):
    (tmp_path / "bad.py").write_text("def (:\n")
    (tmp_path / "ok.py").write_text("import six\n")
    g = impact.build(tmp_path)
    assert g["parse_errors"][0]["file"] == "bad.py" and "six" in g["packages"]


def test_real_linkwatch_graph_matches_known_facts():
    root = Path(__file__).parents[2]
    g = impact.build(root, {"requests", "jinja2"})["packages"]
    assert "linkwatch/report.py" in g["jinja2"]["direct_files"]
    assert any(t.startswith("tests/") for t in g["jinja2"]["tests_direct"] + [x["file"] for x in g["jinja2"]["tests_indirect"]])
    assert {"linkwatch/checker.py", "linkwatch/encoding.py"} <= set(g["requests"]["direct_files"])
