"""GitHub client + publishing gates against an in-memory fake GitHub. No network, no real token."""
import base64
import io
import json
import re
import urllib.error
import zipfile
from email.message import Message

import pytest

from fakes import BASE_SHA, TOKEN, FakeGitHub, Resp, http_error
from depguard.git_integration import github, pull_request as pr
from depguard.git_integration.github import GitHubClient, GitHubError



@pytest.fixture
def gh():
    return FakeGitHub()


def client(gh, token=TOKEN):
    return GitHubClient(token, opener=gh)


# ---------------------------------------------------------------- reading
def test_token_is_sent_only_as_bearer_header_and_never_in_urls_or_bodies(gh):
    c = client(gh)
    c.user(); c.repo("o", "r"); c.branches("o", "r"); c.commit_sha("o", "r", "main")
    for method, url, auth, body in gh.calls:
        assert auth == f"Bearer {TOKEN}" and TOKEN not in url and TOKEN not in json.dumps(body)


def test_repo_branches_and_commit(gh):
    c = client(gh)
    assert c.repo("o", "r")["default_branch"] == "main" and c.branches("o", "r")[0] == {"name": "main", "sha": BASE_SHA}
    assert c.commit_sha("o", "r", "main") == BASE_SHA


@pytest.mark.parametrize("owner,name", [("../x", "r"), ("o", "r/../x"), ("o", ""), ("o w", "r"), ("o", "r;rm")])
def test_invalid_names_never_reach_the_network(gh, owner, name):
    with pytest.raises(GitHubError) as e:
        client(gh).repo(owner, name)
    assert e.value.kind == "invalid" and gh.calls == []


def test_error_mapping_is_specific_and_never_leaks_the_token():
    def boom(code, body=b"{}", headers=None):
        def op(req, timeout=0):
            raise http_error(req.full_url, code, body, headers)
        return op
    cases = [(401, "auth", {}), (404, "not_found", {}), (403, "permission", {}), (422, "conflict", {}),
             (403, "rate_limit", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"}), (500, "api", {})]
    for code, kind, hdr in cases:
        with pytest.raises(GitHubError) as e:
            GitHubClient(TOKEN, opener=boom(code, json.dumps({"message": f"detail {TOKEN}"}).encode(), hdr)).repo("o", "r")
        assert e.value.kind == kind and TOKEN not in str(e.value)
    with pytest.raises(GitHubError, match="authorizing raises the limit"):
        GitHubClient(None, opener=boom(403, b"{}", {"x-ratelimit-remaining": "0"})).repo("o", "r")


def test_network_failure_and_garbage_response():
    def down(req, timeout=0):
        raise urllib.error.URLError(f"no route {TOKEN}")
    with pytest.raises(GitHubError) as e:
        GitHubClient(TOKEN, opener=down).repo("o", "r")
    assert e.value.kind == "network" and TOKEN not in str(e.value)
    with pytest.raises(GitHubError, match="non-JSON"):
        GitHubClient(TOKEN, opener=lambda req, timeout=0: Resp(b"<html>")).user()


def test_redact_patterns():
    assert github.redact(f"x {TOKEN} y github_pat_" + "Z" * 30) == "x [redacted-token] y [redacted-token]"
    assert "secret" not in github.redact("my secret thing", "secret")


# ---------------------------------------------------------------- source archive
def zip_of(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return b.getvalue()


def test_zip_download_follows_redirect_without_credentials(gh, tmp_path):
    gh.zip_bytes = zip_of({"o-r-aaaa/requirements.txt": "six\n"})
    client(gh).download_zip("o", "r", BASE_SHA, tmp_path / "r.zip")
    api_call, cdn_call = gh.calls[0], gh.calls[1]
    assert api_call[2] == f"Bearer {TOKEN}" and "codeload.example" in cdn_call[1] and cdn_call[2] is None  # token NOT forwarded to the second host
    from depguard import repo_input
    assert repo_input.validate_zip(tmp_path / "r.zip")["names"] == ["o-r-aaaa/requirements.txt"]


def test_zip_download_requires_full_sha_and_respects_size_cap(gh, tmp_path):
    with pytest.raises(GitHubError, match="40-character"):
        client(gh).download_zip("o", "r", "main", tmp_path / "x.zip")
    gh.zip_bytes = b"x" * 5000
    with pytest.raises(GitHubError, match="larger than"):
        client(gh).download_zip("o", "r", BASE_SHA, tmp_path / "x.zip", max_bytes=1000)


# ---------------------------------------------------------------- publishing
FILES = {"requirements.txt": b"jinja2==3.1.6\n"}
PLAN_FILES = [{"path": "requirements.txt", "status": "modified"}]
VERIFIED = {"status": "passed", "summary": "10 passed / 0 failed"}


def make_plan(gh, **kw):
    return pr.plan(client(gh), "o", "r", kw.get("base", "main"), PLAN_FILES, kw.get("verification", VERIFIED), ["jinja2"])


def test_plan_is_read_only_and_flags_unverified_and_default_branch(gh):
    p = make_plan(gh, verification={"status": "failed"})
    assert p["base_sha"] == BASE_SHA and p["proposed_branch"].startswith("depguard/") and p["can_publish"]
    assert any("not verified" in w for w in p["warnings"]) and any("default branch is never modified" in w for w in p["warnings"])
    assert all(c[0] == "GET" for c in gh.calls)  # planning wrote nothing


def test_plan_blocks_without_push_permission_or_token():
    assert make_plan(FakeGitHub(push=False))["can_publish"] is False
    p = pr.plan(GitHubClient(None, opener=FakeGitHub()), "o", "r", "main", PLAN_FILES, VERIFIED)
    assert p["can_publish"] is False and "Not authorized" in p["publish_blocker"]


def test_publish_requires_the_exact_phrase_and_writes_nothing_without_it(gh):
    p = make_plan(gh)
    n = len(gh.calls)
    for bad in ("", "publish", "yes", "PUBLISH "):
        with pytest.raises(GitHubError, match="approval missing"):
            pr.publish_branch(client(gh), "o", "r", p, FILES, "msg", bad)
    assert len(gh.calls) == n and gh.refs == {}


def test_publish_creates_one_commit_on_a_new_branch_from_the_reviewed_sha(gh):
    p = make_plan(gh)
    out = pr.publish_branch(client(gh), "o", "r", p, FILES, "Fix jinja2", "PUBLISH")
    writes = [(m, u.split("/repos/o/r")[1]) for m, u, a, b in gh.calls if m == "POST"]
    assert [w[1] for w in writes] == ["/git/blobs", "/git/trees", "/git/commits", "/git/refs"]
    commit_body = [b for m, u, a, b in gh.calls if u.endswith("/git/commits") and m == "POST"][0]
    assert commit_body["parents"] == [BASE_SHA] and out["branch"] == p["proposed_branch"] and gh.refs == {f"refs/heads/{p['proposed_branch']}": "c" * 40}
    assert not any(m in ("PATCH", "PUT", "DELETE") for m, *_ in gh.calls)  # never updates or force-pushes an existing ref


def test_publish_fails_if_base_branch_moved(gh):
    p = make_plan(gh)
    gh.base_sha = "b" * 40
    with pytest.raises(GitHubError, match="base branch moved"):
        pr.publish_branch(client(gh), "o", "r", p, FILES, "m", "PUBLISH")
    assert gh.refs == {}


def test_publish_refuses_default_branch_changed_files_and_existing_branch(gh):
    p = make_plan(gh)
    bad = dict(p, proposed_branch="main")
    with pytest.raises(GitHubError, match="default or base"):
        pr.publish_branch(client(gh), "o", "r", bad, FILES, "m", "PUBLISH")
    with pytest.raises(GitHubError, match="differ from the reviewed plan"):
        pr.publish_branch(client(gh), "o", "r", p, {**FILES, "extra.py": b"x"}, "m", "PUBLISH")
    pr.publish_branch(client(gh), "o", "r", p, FILES, "m", "PUBLISH")
    with pytest.raises(GitHubError) as e:  # same branch again: GitHub says it exists; DepGuard does not overwrite it
        pr.publish_branch(client(gh), "o", "r", p, FILES, "m", "PUBLISH")
    assert e.value.kind == "conflict"


def test_unsafe_paths_and_branch_names_are_rejected(gh):
    c = client(gh)
    with pytest.raises(GitHubError, match="unsafe path"):
        c.commit_to_new_branch("o", "r", BASE_SHA, "depguard/x", {"../evil": b"x"}, "m")
    for b in ("../x", "a..b", "x.lock", "x/", "-x", "a b", ""):
        with pytest.raises(GitHubError, match="invalid branch"):
            c.commit_to_new_branch("o", "r", BASE_SHA, b, FILES, "m")


def test_pull_request_needs_pushed_branch_and_its_own_approval(gh):
    p = make_plan(gh)
    with pytest.raises(GitHubError, match="no pushed branch"):
        pr.open_pull_request(client(gh), "o", "r", p, None, "t", "b", "OPEN PR")
    branch = pr.publish_branch(client(gh), "o", "r", p, FILES, "m", "PUBLISH")
    n = len(gh.calls)
    with pytest.raises(GitHubError, match="approval missing"):
        pr.open_pull_request(client(gh), "o", "r", p, branch, "t", "b", "PUBLISH")  # the first approval does not authorise the PR
    assert len(gh.calls) == n
    out = pr.open_pull_request(client(gh), "o", "r", p, branch, "t", "b", "OPEN PR")
    assert out["html_url"].endswith("/pull/7")
    pr_call = [c for c in gh.calls if c[1].endswith("/pulls")][0]
    assert pr_call[3]["head"] == p["proposed_branch"] and pr_call[3]["base"] == "main"
    assert not any(c[1].endswith("/merge") for c in gh.calls)  # never merges


def test_pr_body_states_verification_honestly():
    failed = pr.pr_body(["jinja2 upgrade"], "why", "1 file", {"status": "failed", "summary": "exit 4"}, ["gap"], "capsule.json")
    assert "NOT verified as working" in failed and "**failed**" in failed and "- gap" in failed
    ok = pr.pr_body(["x"], "", "", {"status": "passed"}, [], "c")
    assert "does not prove full compatibility" in ok and "none recorded" in ok
