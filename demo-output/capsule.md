# Dependency Exit Capsule: LinkWatch

Generated 2026-10-15T07:25:41+00:00 · commit `f52f3930c8e7c33d934ccf614955a38e26984bd5` · dirty: True

## 1. Observed facts (deterministic)

Manifests: requirements-dev.txt, requirements.txt, pyproject.toml · requires-python: `>=3.10`

| Package | Declared | Pinned | Resolved | Level | Contributing |
|---|---|---|---|---|---|
| pytest | `-` (requirements-dev.txt:1) | - | 9.1.1 | no_issue_observed | - |
| nose | `==1.3.7` (requirements-dev.txt:2) | 1.3.7 | 1.3.7 | no_issue_observed | - |
| toml | `==0.10.2` (requirements.txt:1) | 0.10.2 | 0.10.2 | no_issue_observed | - |
| appdirs | `==1.4.4` (requirements.txt:2) | 1.4.4 | 1.4.4 | no_issue_observed | - |
| requests | `==2.31.0` (requirements.txt:3) | 2.31.0 | 2.31.0 | medium | private_api_import |
| jinja2 | `==2.11.3` (requirements.txt:4) | 2.11.3 | 2.11.3 | high | observed_in_baseline_output |
| pydantic | `==1.10.13` (requirements.txt:5) | 1.10.13 | 1.10.13 | medium | conflicting_constraints |
| click | `>=7` (requirements.txt:6) | - | 8.5.0 | no_issue_observed | - |
| six | `-` (requirements.txt:7) | - | 1.17.0 | no_issue_observed | - |
| chardet | `==5.2.0` (requirements.txt:8) | 5.2.0 | 5.2.0 | no_issue_observed | - |
| setuptools | `==75.8.0` (requirements.txt:9) | 75.8.0 | 75.8.0 | high | observed_in_baseline_output |
| idna | `==2.7` (requirements.txt:10) | 2.7 | 2.7 | no_issue_observed | - |
| python-dateutil | `-` (requirements.txt:11) | - | 2.9.0.post0 | no_issue_observed | - |
| rich | `-` (requirements.txt:12) | - | 15.0.0 | no_issue_observed | - |
| toml | `==0.10.2` (pyproject.toml:13) | 0.10.2 | 0.10.2 | no_issue_observed | - |
| appdirs | `==1.4.4` (pyproject.toml:14) | 1.4.4 | 1.4.4 | no_issue_observed | - |
| requests | `==2.31.0` (pyproject.toml:15) | 2.31.0 | 2.31.0 | medium | private_api_import |
| jinja2 | `==2.11.3` (pyproject.toml:16) | 2.11.3 | 2.11.3 | high | observed_in_baseline_output |
| pydantic | `>=2` (pyproject.toml:17) | - | 1.10.13 | medium | conflicting_constraints |
| click | `>=7` (pyproject.toml:18) | - | 8.5.0 | no_issue_observed | - |
| six | `-` (pyproject.toml:19) | - | 1.17.0 | no_issue_observed | - |
| chardet | `==5.2.0` (pyproject.toml:20) | 5.2.0 | 5.2.0 | no_issue_observed | - |
| setuptools | `==75.8.0` (pyproject.toml:21) | 75.8.0 | 75.8.0 | high | observed_in_baseline_output |
| idna | `==2.7` (pyproject.toml:22) | 2.7 | 2.7 | no_issue_observed | - |
| python-dateutil | `-` (pyproject.toml:23) | - | 2.9.0.post0 | no_issue_observed | - |
| rich | `-` (pyproject.toml:24) | - | 15.0.0 | no_issue_observed | - |

### Evidence

- **pydantic** · conflicting_constraints (warn): requirements.txt pins 1.10.13 but pyproject.toml requires '>=2'
- **certifi** · imported_but_undeclared (warn): linkwatch/checker.py:4
- **requests** · private_api_import (warn): linkwatch/encoding.py:5 imports requests.utils._parse_content_type_header
- **jinja2** · observed_in_baseline_output (high): E   ImportError: cannot import name 'soft_unicode' from 'markupsafe' (<tmp>\venv\Lib\site-packages\markupsafe\__init__.py); ..\venv\Lib\site-packages\jinja2\filters.py:13: ImportError
- **setuptools** · observed_in_baseline_output (high): <tmp>\repo\linkwatch\resources.py:1: DeprecationWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html

### Usage

- Undeclared imports: ['certifi']
- No static import found (not proof of non-use): ['idna', 'nose', 'six']
- Dynamic import sites: 0

- `appdirs`: linkwatch/cache.py:4
- `certifi`: linkwatch/checker.py:4
- `requests`: linkwatch/checker.py:5, linkwatch/checker.py:6, linkwatch/encoding.py:5, linkwatch/http_patch.py:1
- `click`: linkwatch/cli.py:1, tests/test_report.py:1
- `rich`: linkwatch/cli.py:2, linkwatch/cli.py:3, linkwatch/cli.py:4
- `toml`: linkwatch/config.py:3
- `pydantic`: linkwatch/config.py:4
- `python-dateutil`: linkwatch/dates.py:1
- `chardet`: linkwatch/encoding.py:4
- `jinja2`: linkwatch/report.py:3
- `setuptools`: linkwatch/resources.py:1
- `pytest`: tests/conftest.py:6

### Health score (HEURISTIC, not a guarantee): 92/100 · evidence coverage 75%

Heuristic score from the evidence listed below. It is not an objective measure of safety or quality, excludes vulnerabilities, and unchecked data is not counted against any package.

- **jinja2** 65/100: -35 observed_in_baseline_output (observed)
- **setuptools** 65/100: -35 observed_in_baseline_output (observed)
- **pydantic** 80/100: -20 conflicting_constraints (observed)
- **requests** 90/100: -10 private_api_import (observed)
- **idna** 95/100: -5 no_static_import_found (heuristic)
- **nose** 95/100: -5 no_static_import_found (heuristic)
- **six** 95/100: -5 no_static_import_found (heuristic)
- Unchecked: package index metadata (release recency, Requires-Python, classifiers); repository activity (commits, issues, maintainers); security vulnerabilities

## 2. AI recommendations (model output, NOT verified fact)

Model: `gemma4:e2b` · runtime: Ollama 0.40.4 · 4.6B Q4_K_M · local HTTP API http://127.0.0.1:11434 (no cloud)
Status: **ok** 

### jinja2: upgrade
Observed ImportError suggests incompatibility issues with markupsafe. Upgrading is recommended.

Candidates: Jinja2>=2.0

1. manifest_edits
2. find": "Jinja2==2.11.3"
3. file": "requirements.txt"
- concern: requirements.txt pins 2.11.3 but pyproject.toml requires '>=2'

### setuptools: upgrade
Observed DeprecationWarning regarding pkg_resources, indicating an upgrade is beneficial.

Candidates: setuptools>=60

1. manifest_edits
2. find": "setuptools==75.8.0"
3. file": "requirements.txt"
- concern: requirements.txt pins 75.8.0 but pyproject.toml requires '>=60'

### certifi: investigate
The package is imported but not declared in dependencies. Its necessity and version status are unknown.

Candidates: -


### pydantic: upgrade
There is a conflict between the pinned version (1.10.13) in requirements.txt and the requirement (>=2) in pyproject.toml. Upgrading to satisfy the modern requirement is necessary.

Candidates: pydantic>=2

1. manifest_edits
2. find": "pydantic==1.10.13"
3. file": "requirements.txt"
- concern: requirements.txt pins 1.10.13 but pyproject.toml requires '>=2'

## 3. Migration proposal (not applied)

Status: proposed · accepted 3, rejected 2

```diff
--- a/requirements.txt
+++ b/requirements.txt
@@ -1,7 +1,7 @@
 toml==0.10.2
 appdirs==1.4.4
 requests==2.31.0
-Jinja2==2.11.3
+Jinja2>=2.0
 pydantic==1.10.13
 click>=7
 six
--- a/requirements.txt
+++ b/requirements.txt
@@ -6,7 +6,7 @@
 click>=7
 six
 chardet==5.2.0
-setuptools==75.8.0
+setuptools>=60
 idna==2.7
 python-dateutil
 rich
--- a/requirements.txt
+++ b/requirements.txt
@@ -2,7 +2,7 @@
 appdirs==1.4.4
 requests==2.31.0
 Jinja2==2.11.3
-pydantic==1.10.13
+pydantic>=2
 click>=7
 six
 chardet==5.2.0

```

- rejected edit `pyproject.toml`: edit would make pyproject.toml invalid TOML: Unclosed array (at line 17, column 5)
- rejected edit `pyproject.toml`: edit would make pyproject.toml invalid TOML: Unclosed array (at line 22, column 5)

## 4. Executed tests (actually run)

- **baseline**: failed
  - `<python> -m venv <tmp>\venv` → exit 0
  - `-r requirements-dev.txt -r requirements.txt` → exit 0
  - `<tmp>\venv\Scripts\python.exe -m pip check` → exit 0
  - `-q -p no:cacheprovider --junitxml=<tmp>\junit.xml` → exit 1
- **proposed**: failed
  - `<python> -m venv <tmp>\venv` → exit 0
  - `-r requirements-dev.txt -r requirements.txt` → exit 0
  - `<tmp>\venv\Scripts\python.exe -m pip check` → exit 0
  - `-q -p no:cacheprovider --junitxml=<tmp>\junit.xml` → exit 4
- comparison: {"baseline_status": "failed", "proposed_status": "failed", "note": "proposed run produced no per-test results (e.g. collection/import error): 'regressed' lists every baseline-passing test", "fixed": [], "regressed": ["tests.test_cache::test_cache_round_trip", "tests.test_checker::test_closed_port_is_down", "tests.test_checker::test_latin1_title", "tests.test_checker::test_missing_is_down", "tests.test_checker::test_ok_is_up", "tests.test_checker::test_redirect_is_up", "tests.test_checker::test_slow_is_up", "tests.test_checker::test_user_agent", "tests.test_config::test_sites_file_loads"], "baseline_counts": {"passed": 9, "failed": 1, "skipped": 0}, "proposed_counts": {"passed": 0, "failed": 0, "skipped": 0}}

### Independent experiment: jinja2-only (not AI; human-specified experiment, independent of the Gemma recommendation)

```diff
--- a/requirements.txt
+++ b/requirements.txt
@@ -1,7 +1,7 @@
 toml==0.10.2
 appdirs==1.4.4
 requests==2.31.0
-Jinja2==2.11.3
+Jinja2==3.1.6
 pydantic==1.10.13
 click>=7
 six
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -13,7 +13,7 @@
     "toml==0.10.2",
     "appdirs==1.4.4",
     "requests==2.31.0",
-    "Jinja2==2.11.3",
+    "Jinja2==3.1.6",
     "pydantic>=2",
     "click>=7",
     "six",

```
- **baseline**: failed · counts {'passed': 9, 'failed': 1, 'skipped': 0}
  - `<python> -m venv <tmp>\venv` → exit 0
  - `-r requirements-dev.txt -r requirements.txt` → exit 0
  - `<tmp>\venv\Scripts\python.exe -m pip check` → exit 0
  - `-q -p no:cacheprovider --junitxml=<tmp>\junit.xml` → exit 1
- **proposed**: passed · counts {'passed': 10, 'failed': 0, 'skipped': 0}
  - `<python> -m venv <tmp>\venv` → exit 0
  - `-r requirements-dev.txt -r requirements.txt` → exit 0
  - `<tmp>\venv\Scripts\python.exe -m pip check` → exit 0
  - `-q -p no:cacheprovider --junitxml=<tmp>\junit.xml` → exit 0
- comparison: {"baseline_status": "failed", "proposed_status": "passed", "note": null, "fixed": ["tests.test_report::test_report_renders_all_sites"], "regressed": [], "baseline_counts": {"passed": 9, "failed": 1, "skipped": 0}, "proposed_counts": {"passed": 10, "failed": 0, "skipped": 0}}

### Suggested tests (model, not run)

- Run tests to ensure compatibility with the upgraded Jinja2 version.
- Run tests to ensure compatibility with the upgraded setuptools version.
- Determine if certifi is required and add it to the dependency list.
- Run tests to ensure compatibility with Pydantic v2.

## 5. Compatibility gaps

- [observed] requirements.txt pins 1.10.13 but pyproject.toml requires '>=2'
- [observed] private API import requests.utils._parse_content_type_header at linkwatch/encoding.py:5
- [observed] rejected AI edit for pyproject.toml: edit would make pyproject.toml invalid TOML: Unclosed array (at line 17, column 5)
- [observed] rejected AI edit for pyproject.toml: edit would make pyproject.toml invalid TOML: Unclosed array (at line 22, column 5)
- [observed] proposed change regressed tests: ['tests.test_cache::test_cache_round_trip', 'tests.test_checker::test_closed_port_is_down', 'tests.test_checker::test_latin1_title', 'tests.test_checker::test_missing_is_down', 'tests.test_checker::test_ok_is_up', 'tests.test_checker::test_redirect_is_up', 'tests.test_checker::test_slow_is_up', 'tests.test_checker::test_user_agent', 'tests.test_config::test_sites_file_loads']
- [AI-claimed, unverified] requirements.txt pins 2.11.3 but pyproject.toml requires '>=2'
- [AI-claimed, unverified] requirements.txt pins 75.8.0 but pyproject.toml requires '>=60'
- [AI-claimed, unverified] requirements.txt pins 1.10.13 but pyproject.toml requires '>=2'

## 6. Limitations

- Maintenance activity / release recency was not checked (no package-index metadata queried).
- Vulnerability scanning is intentionally out of scope.
- Verification uses a throwaway venv and copied tree, not a container; target code runs as the current user.
- AI recommendations are model output and are not facts; only 'executed_tests' reflect real runs.
- Static AST analysis only; imports inside strings, plugins, entry points or dynamic import calls are not resolved.
- 'no_import_found' means no static import was seen; it does NOT prove the package is unused (transitive, runtime or tooling use is possible).
- Import-name to distribution mapping uses a small static alias table; unusual mappings may be misreported as undeclared.
