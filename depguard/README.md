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
| Model | `gemma4:e2b` (as reported by `ollama show`: family gemma4, 4.6B parameters, Q4_K_M; 4.6 GB per `ollama list`) |
| Runtime | [Ollama](https://ollama.com) 0.40.4 on `127.0.0.1:11434` |
| Source | https://ollama.com/library/gemma4 (pulled once; inference is fully local) |
| License | Apache 2.0 per the license text bundled with the local model (`ollama show`; excerpt stored in each capsule). Not cross-checked against Google's official terms; the Ollama library page does not state a license. |
| Hardware used | laptop with an RTX 3050 (4 GB VRAM) and 15 GB RAM. Measured on 15 Oct 2026 with `ollama ps`: the Ollama instance started at login ran the model 100% on the CPU (it had not detected the GPU); a freshly started Ollama placed it 100% on the GPU |

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

## Web dashboard

A local, dependency-free dashboard (stdlib `http.server` + plain HTML/CSS/JS, no build step, no CDN). It is organised around the product workflow:
**Repository → Findings → Ask Gemma 4 → Repair → Verify → Publish → Reports & capsule.**

```bash
python -m venv .venv                                                    # Python 3.12/3.13
.venv\Scripts\python -m pip install -r depguard/requirements.txt        # macOS/Linux: .venv/bin/python
.venv\Scripts\python -m depguard.web                                    # opens http://127.0.0.1:8765/
# flags: --port N  --no-browser  --offline  --allow PATH (scan root)  --allow-apply PATH (extra apply target)
```

| Page | What it shows (all from recorded data; empty/error states otherwise) |
|---|---|
| Workflow overview | the seven steps with real status, a **verification failed** banner, verification outcomes (baseline, patches, experiments), findings with the status of any tested fix, missing evidence |
| 1 · Repository | open a **ZIP**, a **local repository**, or a **GitHub** repository + branch (exact commit recorded); active repository card; analysis options and progress |
| 2 · Findings | severity summary, filters/sort, one row per at-risk dependency; details with evidence (kind, source, retrieval time), usage, impact graph, unknowns, AI candidates; **Repair / Ignore / Review later**; Mode B batch |
| 3 · Ask Gemma 4 | recommendations as unverified AI claims, apart from independently verified experiments, with All/Unverified/Passed/Failed filters |
| 4 · Repair workspace | patches: per-file diff, reason, evidence, origin (AI or deterministic), advisory-coverage check, assumptions/concerns/tests, verification, approve/revise/reject/apply/rollback, attempt log |
| 5 · Verify | patch verification (commands, exit codes, durations, environment), baseline vs proposed, experiments, limits |
| 6 · Publish | local `.patch` export, repository state, pre-publish checks, GitHub plan → push branch → open PR (two approvals) |
| 7 · Reports & capsule | schema validation, rendered summary, observed/AI/verified sections, HTML/JSON/Markdown, per-dependency capsules |
| More | Dependency inventory (+ heuristic health score), Replacement comparison, Scan history, Quick scan & clone, Experiments (apply demo edits), LinkWatch status |

### Findings and severity
One finding per at-risk dependency (low "signal" findings are listed but not sent for replacement advice). Severity rules are documented in [`findings.py`](findings.py) and embedded in each capsule
(`severity_criteria`). Every evidence item has a kind (`observed` from files/tests, `metadata` from the package index with source URL and retrieval time, `heuristic`, `unknown`).
Exact-version advisories come from the index's own `vulnerabilities` data for that release (opt-in fetch, cached); an empty list means "none listed", never "secure".

### Repair
* **Mode A (user-triggered):** Repair on one finding → one proposal (deterministic if possible, e.g. declaring an imported-but-undeclared package; otherwise Gemma 4) in a separate working copy → you read the diff → *Accept & test in isolation* or *Request revision* or *Reject* → approve apply.
* **Mode B (prepare automatically):** you tick findings and explicitly enable it; for each, DepGuard proposes → validates → verifies, and revises after a failure using the failure output as data, up to the configurable attempt limit (1–5, default 2). It never applies anything.
* **Every attempt is recorded** (mode, origin, outcome). Stops on: verified, attempt limit, blocked environment, model unavailable/invalid, "no reliable recommendation".
* **Verification outcomes** are never optimistic: `verified` (all tests pass, none skipped, none lost), `partially_verified` (skipped/fewer tests, or fixes some failures with no regressions), `failed` (a regression or still failing), `blocked` (environment), `not_run`.
* **Advisory coverage:** after a patch relaxes or pins a version, its lowest allowed version is compared with the fixed-in versions the index lists, and a warning shows if affected releases are still permitted.

### Repository inputs
* **ZIP:** raw upload (≤ 50 MB). Rejects `..`, absolute/drive/UNC paths, reserved device names, symlinks, encrypted archives, > 20 000 entries, > 250 MB extracted, extreme compression ratios; not-a-project archives are refused and cleaned up.
* **Local Git:** branch, commit, modified/untracked files recorded; a copy of the current tree (with your uncommitted changes) is the working copy; `.git` is not copied.
* **GitHub:** repo + branches via the REST API; the exact commit's source archive is downloaded (the credential is not forwarded to the redirect host) and extracted like a ZIP.

### Publication
Local export always works. GitHub: a plan (repository, base branch + SHA, new branch name, files, verification, warnings, blockers) → approval 1 `PUBLISH` creates one commit on a new branch via the Git Data API (fails if the base branch moved
or the pushed bytes differ from the reviewed patch) → approval 2 `OPEN PR` opens the pull request. The pull request body states verification honestly and never claims compatibility.

### Safety model, offline mode, limits
See the top-level README. In short: the analysed repository is never modified; nothing applies or publishes without typed approvals; localhost-only; no shell endpoint; the GitHub token lives in memory only.
`--offline` disables every network use (GitHub, package index) and the dashboard says so. History is stored in `depguard/web/runs/history.json` (git-ignored; summaries only).

### Health score (heuristic)
Per package, 100 minus documented deductions; repository score = mean of package scores minus repository-level deductions. Weights live in
[`depguard/health.py`](health.py) and are shown in the UI. *Observed*: baseline-output error/deprecation naming the package (high 35 / warn 15),
conflicting constraints 20, imported-but-undeclared 15, private-API import 10, inactive classifier 25, Requires-Python excludes 3.12/3.13 30,
`pip check` conflicts 30 (repo level). *Heuristic*: no static import found 5 (not proof of non-use), latest release older than 730 days 10.
Unknown or unchecked data (package metadata not fetched, baseline not run, repository activity) is **listed as unchecked and never deducted**.
Security vulnerabilities are out of scope. The score is a heuristic, not a guarantee.

### Offline mode and package metadata
Scanning, reports, history and Gemma 4 advice work with no network. Package metadata (latest version/upload time, Requires-Python, requirements,
classifiers) comes from the public package index **only when you click "Fetch metadata"** (or pass `--metadata` to the CLI); every cache entry records its source URL and
timestamp, failures are shown as "unavailable" (never guessed), and `--offline` makes the cache the only source. Full analysis still needs network for `pip install`.
Maintenance activity beyond the latest release date (commits, issues) is never checked.

### Replacement comparison
Rows: local availability, package metadata, supported Python versions, declared requirements (conflicts against the repo's pins are computed), the **license as declared in
the package metadata** (not legal advice; unknown when metadata was not fetched), API/import surface in use, migration effort (observed counts + a labelled rule-of-thumb bucket)
and executed test results (recorded experiments **and the verification of repair patches**; a replacement nobody ran tests for says "not run"). Every cell carries its basis (observed / package metadata / heuristic /
unknown). It **never claims equivalence**: API compatibility of a different package is always reported as unchecked unless a test run covers it.

### Migration safety
* Edits come from the capsule on the server by proposal id, never from the browser; each edit's `find` must match once and must not leave `pyproject.toml` invalid TOML or a requirements file unparseable (rejected before any test run).
* Apply and rollback are limited to **isolated experiment copies** created by the dashboard (or paths you pass with `--allow-apply`). The scanned repository is never an apply target by default.
* Typed `APPLY` + a confirm dialog; a snapshot is written to `.depguard-backup/<time>/` first; the real diff is compared with the preview; typed `ROLLBACK` restores the snapshot. Nothing is committed or pushed.

### Reports
`capsule.json`, `capsule.md` and a self-contained `capsule.html` are all rendered from the one validated capsule (HTML escapes every repository-derived string and contains no scripts).

### Dashboard safety and limits
Binds `127.0.0.1` only; rejects foreign `Host`/`Origin` and POSTs without an `X-DepGuard` header; no shell or arbitrary-command endpoint; paths must be inside authorized roots; package names are validated before any index URL is built.
It is a single-user local tool without login. A running job shows what it is doing now (`1/6 scanning manifests` … `5/6 asking Gemma 4: 2 of 3 at-risk dependencies` … `6/6 building and validating the capsule`; each verification step is named before it starts), with secrets redacted. History is stored in `depguard/web/runs/history.json` (git-ignored; summaries only, no source or credentials).

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
web/           local dashboard (server.py API + static/ UI)
acquire.py     safe GitHub clone (strict URL, hardened shallow clone, size cap, contained cleanup)
repo_input.py  ZIP validation/extraction, local Git state, working copy of the current tree, content fingerprint
git_integration/github.py, pull_request.py   GitHub REST client (token in memory only) and the two-approval publication flow
findings.py    one finding per at-risk dependency: severity rules, evidence kinds, unknowns
impact.py      dependency -> module -> test graph (static, with stated uncertainty)
repair/        patch.py (validate, base-revision check, apply, export), planner.py (modes A/B, retries, classification), state.py (decisions/attempts), advisory.py
prepublish/    secrets.py (format detection, never echoes values), hygiene.py, review.py
walk.py        one pruned directory walker (never enters .venv/node_modules)
web/project.py, web/api.py   per-project orchestration and HTTP handlers
health.py      transparent heuristic health score (secondary)
metadata.py    opt-in package-index metadata cache (source + time)
compare.py     evidence-only replacement comparison
history.py     local scan history (JSON)
report_html.py self-contained HTML report from the capsule
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
  longer collect. The capsule honestly records the AI proposal as *failed verification* (pytest exit 4, 0 tests collected).
* **Independent experiment (not AI):** `python -m depguard.experiments.jinja2_only . demo-output/capsule.json` changes **only**
  `Jinja2==2.11.3` to `Jinja2==3.1.6` (in `requirements.txt` and `pyproject.toml`, in a temp copy), keeping the
  setuptools and pydantic constraints unchanged. The version was taken from package metadata (Requires-Python `>=3.7`,
  `MarkupSafe>=2.0`; the resolver picked MarkupSafe 3.0.4), not guessed. Separate temp copies and venvs, Python 3.12.10:

  | | exit | pytest result | notes |
  |---|---|---|---|
  | baseline (Jinja2 2.11.3) | 1 | 1 failed, 9 passed | `ImportError: cannot import name 'soft_unicode' from 'markupsafe'` |
  | proposed (Jinja2 3.1.6) | 0 | 10 passed | `test_report_renders_all_sites` fixed, no regressions; `pip check` clean |

  This shows the suite passes with that one change. It does **not** prove full compatibility: the only test touching Jinja2
  (`test_report_renders_all_sites`) just asserts that the site names appear in the rendered page, the `pkg_resources` deprecation warning is still present, and the pydantic
  conflict and undeclared `certifi` are untouched. The capsule stores this under `independent_experiments`,
  separate from Gemma's recommendation (`ai_recommendations`) and Gemma's own failed verification (`executed_tests`).

## Tests

```bash
pytest depguard/tests -k "not live"            # 366 tests (Python 3.12 and 3.13): mocked AI/GitHub/verifier, no network, ~2 min
pytest depguard/tests/test_live_gemma.py       # real local Gemma 4 inference (skipped if model absent)
pytest                                         # LinkWatch's own suite on the ORIGINAL tree (1 known failure: Jinja2 2.11.3)
```

Covered: scanner, usage mapping, indicators, invalid/malformed/timed-out AI responses, path-escape and ambiguous edits, approval gate, snapshot/diff-check/rollback,
verification states and failure kinds, health-score rules, metadata cache (offline, failures, corruption), replacement comparison, scan history (corruption, concurrency, safe delete),
capsule schema validation and secret rejection, HTML escaping, GitHub URL validation and clone hardening, the untrusted-code guard, the workflow overview and advisor page logic (run under Node against the real demo capsule), and the dashboard API (host/CSRF guards, authorized roots, isolated apply, downloads).

## Limitations

* Package-index metadata (latest release date, Requires-Python, requirements) is fetched **only on explicit request** and was not fetched for the committed demo capsule (shown as "not checked"); repository activity (commits, issues, maintainers) is never checked. Neither is guessed.
* The health score is a heuristic over the evidence available; low evidence coverage means the score says little. Vulnerabilities are out of scope.
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
