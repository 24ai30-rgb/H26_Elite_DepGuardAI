"""Minimal GitHub REST client (stdlib only).

* The token lives only in this object's memory; it is sent in the Authorization header and never logged, returned or included in errors.
* The opener is injectable so every call is testable without a network.
* Source ZIPs are downloaded from the redirect target WITHOUT the Authorization header.
"""
import base64
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
TOKEN_RE = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


class GitHubError(Exception):
    """kind: auth | permission | not_found | rate_limit | network | conflict | api | invalid"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def redact(text: str, token: str | None = None) -> str:
    out = TOKEN_RE.sub("[redacted-token]", str(text))
    return out.replace(token, "[redacted-token]") if token else out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def default_opener(req, timeout=30):
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)


def valid_repo(owner: str, name: str) -> None:
    if not NAME_RE.match(owner or "") or not NAME_RE.match(name or "") or ".." in owner + name:
        raise GitHubError("invalid", "invalid owner or repository name")


def valid_branch(branch: str) -> None:
    if not BRANCH_RE.match(branch or "") or ".." in branch or branch.endswith("/") or branch.endswith(".lock") or "//" in branch:
        raise GitHubError("invalid", "invalid branch name")


class GitHubClient:
    def __init__(self, token: str | None = None, opener=default_opener, base: str = API):
        self._token = token or None
        self._opener = opener
        self._base = base

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _headers(self, accept="application/vnd.github+json", auth=True):
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "DepGuard-AI"}
        if auth and self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _map_http(self, e: urllib.error.HTTPError):
        try:
            msg = json.loads(e.read() or b"{}").get("message", "")
        except (ValueError, OSError):
            msg = ""
        msg = redact(msg, self._token)[:200]
        remaining = e.headers.get("x-ratelimit-remaining") if e.headers else None
        if e.code in (403, 429) and (remaining == "0" or "rate limit" in msg.lower()):
            reset = e.headers.get("x-ratelimit-reset") if e.headers else None
            when = datetime.fromtimestamp(int(reset), timezone.utc).strftime("%H:%M UTC") if reset and reset.isdigit() else "later"
            raise GitHubError("rate_limit", f"GitHub rate limit reached; try again after {when}" + ("" if self._token else " (authorizing raises the limit)"))
        if e.code == 401:
            raise GitHubError("auth", "GitHub rejected the credentials (invalid or expired token)")
        if e.code == 403:
            raise GitHubError("permission", f"permission denied by GitHub: {msg or 'the token lacks the required permission'}")
        if e.code == 404:
            raise GitHubError("not_found", "not found, or not visible with the current credentials")
        if e.code in (409, 422):
            raise GitHubError("conflict", f"GitHub refused the request: {msg or e.code}")
        raise GitHubError("api", f"GitHub API error {e.code}: {msg}")

    def _json(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._base + path, data=data, method=method, headers={**self._headers(), **({"Content-Type": "application/json"} if data else {})})
        try:
            with self._opener(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            self._map_http(e)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise GitHubError("network", f"cannot reach GitHub: {redact(getattr(e, 'reason', e), self._token)}")
        except ValueError:
            raise GitHubError("api", "unexpected (non-JSON) response from GitHub")

    # ---------------------------------------------------------------- read
    def user(self) -> dict:
        d = self._json("GET", "/user")
        return {"login": d.get("login")}

    def repo(self, owner: str, name: str) -> dict:
        valid_repo(owner, name)
        d = self._json("GET", f"/repos/{owner}/{name}")
        return {"full_name": d["full_name"], "default_branch": d["default_branch"], "private": d["private"], "html_url": d["html_url"],
                "permissions": d.get("permissions", {}), "fork": d.get("fork", False)}

    def branches(self, owner: str, name: str, limit: int = 100) -> list:
        valid_repo(owner, name)
        d = self._json("GET", f"/repos/{owner}/{name}/branches?per_page={min(limit, 100)}")
        return [{"name": b["name"], "sha": b["commit"]["sha"]} for b in d]

    def commit_sha(self, owner: str, name: str, ref: str) -> str:
        valid_repo(owner, name)
        valid_branch(ref)
        return self._json("GET", f"/repos/{owner}/{name}/commits/{ref}")["sha"]

    def download_zip(self, owner: str, name: str, sha: str, dest, max_bytes: int = 50 * 1024 * 1024) -> None:
        """Download the archive of one exact commit. Follows the redirect manually and drops the Authorization header for the second hop."""
        valid_repo(owner, name)
        if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            raise GitHubError("invalid", "a full 40-character commit SHA is required")
        req = urllib.request.Request(f"{self._base}/repos/{owner}/{name}/zipball/{sha}", headers=self._headers("application/vnd.github+json"))
        try:
            with self._opener(req, timeout=60) as r:  # no redirect happened: body is the archive (or a test double)
                return self._save(r, dest, max_bytes)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location", "").startswith("https://"):
                try:
                    with self._opener(urllib.request.Request(e.headers["Location"], headers={"User-Agent": "DepGuard-AI"}), timeout=120) as r:
                        return self._save(r, dest, max_bytes)
                except urllib.error.HTTPError as e2:
                    self._map_http(e2)
                except (urllib.error.URLError, OSError, TimeoutError) as e2:
                    raise GitHubError("network", f"download failed: {redact(getattr(e2, 'reason', e2), self._token)}")
            self._map_http(e)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise GitHubError("network", f"cannot reach GitHub: {redact(getattr(e, 'reason', e), self._token)}")

    @staticmethod
    def _save(resp, dest, max_bytes):
        total = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise GitHubError("invalid", f"archive is larger than {max_bytes // 2**20} MB")
                out.write(chunk)

    # ---------------------------------------------------------------- write (only called after explicit approval)
    def commit_to_new_branch(self, owner, name, base_sha, branch, files: dict, message: str) -> dict:
        """Atomic: blobs -> tree -> commit -> NEW ref. Never updates an existing ref, never forces."""
        valid_repo(owner, name)
        valid_branch(branch)
        if not files:
            raise GitHubError("invalid", "no files to commit")
        base_tree = self._json("GET", f"/repos/{owner}/{name}/git/commits/{base_sha}")["tree"]["sha"]
        entries = []
        for path, content in sorted(files.items()):
            if path.startswith("/") or ".." in path.split("/"):
                raise GitHubError("invalid", f"unsafe path {path!r}")
            blob = self._json("POST", f"/repos/{owner}/{name}/git/blobs", {"content": base64.b64encode(content).decode(), "encoding": "base64"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self._json("POST", f"/repos/{owner}/{name}/git/trees", {"base_tree": base_tree, "tree": entries})
        commit = self._json("POST", f"/repos/{owner}/{name}/git/commits", {"message": message, "tree": tree["sha"], "parents": [base_sha]})
        self._json("POST", f"/repos/{owner}/{name}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})  # 422 if it already exists
        return {"branch": branch, "commit_sha": commit["sha"], "commit_url": f"https://github.com/{owner}/{name}/commit/{commit['sha']}"}

    def create_pull_request(self, owner, name, head, base, title, body) -> dict:
        valid_repo(owner, name)
        valid_branch(head)
        valid_branch(base)
        d = self._json("POST", f"/repos/{owner}/{name}/pulls", {"title": title, "head": head, "base": base, "body": body, "maintainer_can_modify": True})
        return {"number": d["number"], "html_url": d["html_url"]}
