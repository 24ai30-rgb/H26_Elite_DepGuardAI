"""Secret detection by FORMAT. Values are never returned, logged or shown: only type, location, length and a short hash for de-duplication.

confidence "high_format": the text matches the exact format of a known credential type (still not proof it is live).
confidence "suspected":   a heuristic match (e.g. password = "..."), may be a placeholder or test value.
"""
import hashlib
import re
from pathlib import Path

from .. import walk

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".depguard-backup", "dist", "build"}
MAX_BYTES = 1_000_000
PLACEHOLDER = re.compile(r"(example|sample|dummy|placeholder|changeme|your[_-]|<[^>]+>|x{6,}|\*{4,}|test|fake|0{8,}|redacted|todo)", re.I)

PATTERNS = [   # (type, regex, confidence)
    ("aws_access_key_id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "high_format"),
    # same floor as git_integration.github.TOKEN_RE: over-redacting is harmless, missing a token is not
    ("github_token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "high_format"),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"), "high_format"),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), "high_format"),
    ("stripe_live_key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b"), "high_format"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "high_format"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "suspected"),
    ("credential_assignment", re.compile(r"""(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['"]([^'"\s]{8,})['"]"""), "suspected"),
]


def _entry(kind, conf, file, line, value):
    return {"type": kind, "confidence": conf, "file": file, "line": line, "length": len(value),
            "fingerprint": hashlib.sha256(value.encode()).hexdigest()[:8],
            "recommendation": "Remove it from the repository, rotate the credential if it was ever real, and load it from the environment or a secret store instead."
                              if conf == "high_format" else "Check whether this is a real credential; if so, move it out of the repository and rotate it."}


def scan_text(text: str, file: str = "") -> list:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, rx, conf in PATTERNS:
            for m in rx.finditer(line):
                value = m.group(m.lastindex) if kind == "credential_assignment" else m.group(0)
                if PLACEHOLDER.search(value) or PLACEHOLDER.search(line) and conf == "suspected":
                    continue
                out.append(_entry(kind, conf, file, n, value))
    return out


def scan_files(root: Path, files=None) -> list:
    """Scan `files` (relative paths) or every text file under root. Binary/oversized files are skipped and reported by the caller's file list, not read."""
    root = Path(root)
    out = []
    names = files if files is not None else [p.relative_to(root).as_posix() for p in walk.iter_files(root, SKIP_DIRS)]
    for rel in names:
        p = root / rel
        try:
            if not p.is_file() or p.stat().st_size > MAX_BYTES:
                continue
            data = p.read_bytes()
            if b"\x00" in data[:2048]:
                continue
            out += scan_text(data.decode("utf-8", errors="replace"), rel)
        except OSError:
            continue
    return out


def redact(text: str) -> str:
    """Mask anything that matches a secret pattern (for logs, reports, error messages)."""
    for kind, rx, _ in PATTERNS:
        text = rx.sub(lambda m: m.group(0)[: m.start(m.lastindex) - m.start(0)] + f"[REDACTED:{kind}]" if kind == "credential_assignment" else f"[REDACTED:{kind}]", text)
    return text
