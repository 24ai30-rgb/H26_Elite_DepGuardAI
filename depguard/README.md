# DepGuard AI

Dependency-resilience analysis for Python repositories, with a **local Gemma 4** advisor.
Hacktoberfest 2026, Track 1 (Best Use of Gemma 4). Target repository: LinkWatch (this repo).

DepGuard scans the repository's manifests, maps where each package is imported, runs the project's own tests
in a throwaway venv, asks Gemma 4 for replacement/migration advice grounded in that evidence, **verifies the
proposed edits by re-running the tests**, and writes a *Dependency Exit Capsule* (JSON + Markdown).
Security-vulnerability scanning is deliberately out of scope.

## Observed vs. AI vs. verified

The capsule keeps three kinds of content apart:

| Kind | Where | Source |
|---|---|---|
| Observed facts | `dependencies`, `risk_evidence`, `usage` | deterministic parsing / AST / actual test output |
| AI recommendations | `ai_recommendations`, `suggested_tests` | Gemma 4 output, schema-validated, **not facts** |
| Verified results | `executed_tests` | commands, exit codes and output of real runs |

## Setup

Python **3.12 or 3.13** (3.14 is unsupported by LinkWatch's pinned dependencies).

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r depguard/requirements.txt
```

### Gemma 4 model (local only, no cloud, no paid API)

| | |
|---|---|
| Model | `gemma4:e2b` (Gemma 4 "effective 2B", 4.6B parameters with embeddings, Q4_K_M, ~7 GB on disk) |
| Runtime | [Ollama](https://ollama.com) 0.40.4 on `127.0.0.1:11434` |
| Source | https://ollama.com/library/gemma4 (pulled once; inference is fully local) |
| License | Apache 2.0 (license text is read from the local model via `ollama show`; excerpt stored in each capsule) |
| Hardware used | RTX 3050 Laptop 4 GB + 15 GB RAM, partial GPU offload |

```bash
ollama pull gemma4:e2b      # do NOT use the *-cloud tags: they are hosted
```

If Ollama or the model is missing, DepGuard reports `ai_recommendations.status = "blocked"` and still produces
the deterministic parts. There is no non-Gemma fallback presented as AI.

## Usage

```bash
python -m depguard analyze . --out depguard-out        # read-only analysis, tests, Gemma advice, capsule
python -m depguard analyze . --no-ai --no-tests        # deterministic scan only (fast)
python -m depguard apply depguard-out/capsule.json --workdir ../linkwatch-copy   # asks for 'yes' first
```

`analyze` never modifies the analyzed repository: edits are previewed as diffs and tested only in a temp copy.
`apply` writes only into the `--workdir` you name, re-validates every path stays inside it, shows the diff,
and requires typing `yes` (or `--yes`). It never commits or pushes.

## Architecture

```
scanner.py     requirements*.txt + pyproject.toml -> declared deps, pins, conflicts, parse issues
usage.py       AST import mapping (file:line), undeclared imports, private-API imports, dynamic-import sites
verify.py      temp copy -> venv -> pip install -> pip check -> pytest(junit) ; baseline vs proposed
indicators.py  rule-based, explainable indicators from the above (unknown stays unknown)
advisor.py     Ollama /api/chat with JSON-schema constrained output + jsonschema validation + 1 retry
migrate.py     edit {file, find, replace} validation (inside root, `find` unique), diff preview, apply
capsule.py     assemble, validate against capsule.schema.json, secret-pattern guard, JSON + Markdown
cli.py         analyze / apply
```

Flow: scan -> usage -> **baseline tests** -> indicators -> Gemma 4 -> validated edits -> **proposed tests** -> compare -> capsule.

## Demo on the real LinkWatch repository

`python -m depguard analyze . --out demo-output` (output committed in [demo-output/](../demo-output/)).
Results from the run committed there (see `capsule.md` for everything):

* **Baseline (observed):** `9 passed, 1 failed`. The failure is `ImportError: cannot import name 'soft_unicode' from 'markupsafe'`
  raised inside Jinja2 2.11.3, attributed to `jinja2` (high). `pkg_resources` DeprecationWarning attributed to `setuptools`.
* **Other observed facts:** `pydantic==1.10.13` in requirements.txt vs `pydantic>=2` in pyproject.toml; `certifi` imported
  (`linkwatch/checker.py`) but undeclared; private import `requests.utils._parse_content_type_header`
  (`linkwatch/encoding.py:5`); `six`, `idna`, `nose` have no static import (not proof of non-use).
* **Gemma 4 (AI, unverified):** proposed relaxing the `Jinja2`, `setuptools` and `pydantic` pins to lower bounds.
* **Edit guard (deterministic):** in the committed run, two of Gemma's `pyproject.toml` edits dropped a trailing comma and were
  rejected as invalid TOML before any test ran (listed under `rejected_edits`). Model output varies between runs.
* **Verification (actual run):** the proposed edits did **not** fix the suite. Relaxing `setuptools==75.8.0`
  lets pip install a setuptools without `pkg_resources`, so `linkwatch/resources.py` fails to import and tests no
  longer collect. So the capsule honestly records the AI proposal as *failed verification*. A human-reviewed fix
  would likely only upgrade Jinja2 and keep the setuptools pin; that variant has **not** been run.

## Tests

```bash
pytest depguard/tests -k "not live"            # mocked AI, offline, ~3 s
pytest depguard/tests/test_live_gemma.py       # real local Gemma 4 inference (skipped if model absent)
pytest                                         # LinkWatch's own suite (baseline: 1 known failure, see above)
```

Covered: scanner (malformed/missing files, conflicts), usage mapping, indicators, invalid/malformed AI responses,
blocked runtime, path-escape and ambiguous edits, approval gate, verification states and comparison, path scrubbing,
capsule schema validation and secret rejection.

## Limitations

* Maintenance activity / last-release data is **not checked** (no PyPI queries); reported as "not checked", never guessed.
* Static analysis only: dynamic imports, plugins and entry points are not resolved; "no static import found" is not proof of non-use.
* The import-name to package-name mapping is a small static alias table.
* Isolation is a venv plus copied tree, **not a container/sandbox**; the target's tests run as your user.
* Package installation during verification needs the public package index (PyPI); no model or analysis data leaves the machine.
* `gemma4:e2b` is small: it tends to be conservative, can propose edits that fail verification (as seen above), and
  its output is never trusted without schema validation and a test run. Responses are non-deterministic across runs
  even at temperature 0.
* Only `requirements*.txt` and `pyproject.toml` `[project]` dependencies are parsed (no Poetry/Pipfile/lock files; `-r` includes are not followed).
* A model-chosen edit is applied only if its `find` text matches exactly once; others are listed as rejected.

## License

MIT, same as the LinkWatch project (see [LICENSE](../LICENSE)). Gemma 4 weights are Apache 2.0 and are not redistributed here.
