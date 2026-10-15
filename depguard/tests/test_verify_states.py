"""Verification must tell test failures apart from setup failures, record the environment, and never report success on failure."""
import pytest

from depguard import verify


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "requirements.txt").write_text("six\n")
    (tmp_path / "t.py").write_text("")
    return tmp_path


def fake_run(codes, out="3 passed in 0.1s"):
    """codes: exit code per command in order; empty junit means no per-test results."""
    it = iter(codes)

    def run(cmd, cwd, timeout):
        return {"command": [str(c) for c in cmd], "exit_code": next(it), "output": out}
    return run


@pytest.mark.parametrize("codes,status,kind,steps", [
    ([1], "environment_blocked", "venv_failure", 1),                        # venv creation failed
    ([0, 1], "environment_blocked", "install_failure", 2),                  # pip install failed
    ([0, -1], "environment_blocked", "timeout", 2),                         # install timed out
    ([0, 0, 0, 0, 0, 2], "failed", "pytest_collection_or_usage_error", 5),     # order: venv, install, pip --version, pip check, pip list, pytest
    ([0, 0, 0, 0, 0, 1], "failed", "test_failures", 5),                         # real test failures
    ([0, 0, 0, 0, 0, 0], "passed", None, 5),
])
def test_status_and_failure_kind(monkeypatch, repo, codes, status, kind, steps):
    monkeypatch.setattr(verify, "run", fake_run(codes + [0] * 3))
    r = verify._verify(repo, None, "x")
    assert r["status"] == status and r["failure_kind"] == kind and len(r["steps"]) == steps
    env = r["environment"]
    assert env["python"] and env["platform"] and "NOT a container" in env["isolation"]


def test_success_is_never_reported_when_tests_did_not_run(monkeypatch, repo):
    monkeypatch.setattr(verify, "run", fake_run([0, 1]))
    assert verify._verify(repo, None, "x")["status"] != "passed"


def test_edits_applied_only_to_the_copy(monkeypatch, repo):
    monkeypatch.setattr(verify, "run", fake_run([1]))
    verify._verify(repo, [{"file": "requirements.txt", "find": "six", "replace": "six>=1"}], "x")
    assert (repo / "requirements.txt").read_text() == "six\n"
