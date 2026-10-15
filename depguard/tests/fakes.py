"""Shared test doubles: an in-memory fake GitHub (no network, no real token)."""
import io
import json
import re
import urllib.error
from email.message import Message

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
BASE_SHA = "a" * 40


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(url, code, body=b"{}", headers=None):
    h = Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError(url, code, "x", h, io.BytesIO(body))


class FakeGitHub:
    """Routes by method + path. Records (method, url, authorization-header-present, body)."""

    def __init__(self, default_branch="main", push=True, base_sha=BASE_SHA):
        self.calls, self.default_branch, self.push, self.base_sha, self.refs = [], default_branch, push, base_sha, {}
        self.zip_bytes = b""

    def __call__(self, req, timeout=0):
        url, method = req.full_url, req.get_method()
        auth = req.get_header("Authorization")
        body = json.loads(req.data) if req.data else None
        self.calls.append((method, url, auth, body))
        if "codeload.example" in url:
            return Resp(self.zip_bytes)
        path = url.replace("https://api.github.com", "")
        if auth == "Bearer bad":
            raise http_error(url, 401)
        if path == "/user":
            return self.ok({"login": "dev"})
        if re.fullmatch(r"/repos/o/r", path):
            return self.ok({"full_name": "o/r", "default_branch": self.default_branch, "private": False, "html_url": "https://github.com/o/r", "permissions": {"push": self.push}, "fork": False})
        if path.startswith("/repos/o/r/branches"):
            return self.ok([{"name": "main", "commit": {"sha": self.base_sha}}])
        if re.fullmatch(r"/repos/o/r/commits/[\w./-]+", path):
            return self.ok({"sha": self.base_sha})
        if path.startswith("/repos/o/r/zipball/"):
            raise http_error(url, 302, headers={"Location": "https://codeload.example/zip"})
        if path.startswith("/repos/o/r/git/commits/") and method == "GET":
            return self.ok({"tree": {"sha": "t" * 40}})
        if path.endswith("/git/blobs"):
            return self.ok({"sha": "b" * 40})
        if path.endswith("/git/trees"):
            return self.ok({"sha": "e" * 40})
        if path.endswith("/git/commits") and method == "POST":
            return self.ok({"sha": "c" * 40})
        if path.endswith("/git/refs"):
            ref = body["ref"]
            if ref in self.refs:
                raise http_error(url, 422, b'{"message":"Reference already exists"}')
            self.refs[ref] = body["sha"]
            return self.ok({"ref": ref})
        if path.endswith("/pulls"):
            return self.ok({"number": 7, "html_url": "https://github.com/o/r/pull/7"})
        raise http_error(url, 404)

    @staticmethod
    def ok(d):
        return Resp(json.dumps(d).encode())
