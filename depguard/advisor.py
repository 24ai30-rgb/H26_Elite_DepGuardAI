"""Gemma 4 advisor via a local Ollama server (localhost only). No hosted API is ever contacted."""
import json
import urllib.error
import urllib.request
from pathlib import Path

import jsonschema

HOST = "http://127.0.0.1:11434"

RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["recommendations"],
    "properties": {"recommendations": {"type": "array", "items": {
        "type": "object",
        "required": ["package", "action", "replacement_candidates", "rationale", "migration_steps",
                     "compatibility_concerns", "manifest_edits", "suggested_tests"],
        "properties": {
            "package": {"type": "string"},
            "action": {"enum": ["replace", "upgrade", "remove", "keep", "investigate"]},
            "replacement_candidates": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
            "migration_steps": {"type": "array", "items": {"type": "string"}},
            "compatibility_concerns": {"type": "array", "items": {"type": "string"}},
            "manifest_edits": {"type": "array", "items": {
                "type": "object", "required": ["file", "find", "replace"],
                "properties": {k: {"type": "string"} for k in ("file", "find", "replace")}}},
            "suggested_tests": {"type": "array", "items": {"type": "string"}},
        }}}},
}

SYSTEM = (
    "You are a Python dependency-maintenance advisor. You receive OBSERVED facts about a repository. "
    "Recommend, per package, whether to replace/upgrade/remove/keep/investigate it. Rules: use only the "
    "evidence given; do not invent versions, release dates or vulnerabilities; say 'investigate' if unsure. "
    "In manifest_edits, `find` must be an exact, unique substring copied from the provided manifest line "
    "(or provided source line) and `file` the path given; use an empty list if you cannot do this exactly. "
    "Python target is 3.12. "
    "When an observed error/deprecation is attributed to a package pinned with '==', prefer action 'upgrade' and "
    "propose a manifest edit that relaxes the pin to a lower-bound range (e.g. 'name>=MAJOR'); never name an exact "
    "version you were not given. Propose the same edit for each manifest file that declares the package. "
    "Reply with JSON only."
)


def _post(path, body=None, timeout=900):
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def model_info(model: str) -> dict:
    """Facts about the local runtime/model, or a blocker description. Never raises."""
    try:
        ver = json.load(urllib.request.urlopen(HOST + "/api/version", timeout=5))["version"]
        show = _post("/api/show", {"model": model}, timeout=30)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        return {"available": False, "model": model, "blocker": f"Ollama/model not reachable: {e}"}
    d = show.get("details", {})
    lic = show.get("license", "")
    return {"available": True, "model": model, "runtime": "Ollama", "runtime_version": ver,
            "family": d.get("family"), "parameter_size": d.get("parameter_size"),
            "quantization": d.get("quantization_level"), "access": f"local HTTP API {HOST} (no cloud)",
            "source": f"https://ollama.com/library/{model.split(':')[0]}",
            "license_excerpt": " ".join(lic.split())[:200]}


def build_prompt(scan: dict, usage: dict, indicators: dict, root: Path, max_pkgs=4) -> str:
    root = Path(root)
    lvl = indicators["package_levels"]
    rank = sorted((p for p in lvl if p != "*" and lvl[p]["contributing"]),
                  key=lambda p: (lvl[p]["level"] != "high", p))[:max_pkgs]

    def src(file, line):
        try:
            return (root / file).read_text(encoding="utf-8").splitlines()[line - 1].strip()
        except (OSError, IndexError):
            return ""
    facts = []
    for p in rank:
        facts.append({
            "package": p,
            "declared": [{"file": d["file"], "line": d["line"], "text": src(d["file"], d["line"]) or f"{d['name']}{d['specifier']}"}
                         for d in scan["dependencies"] if d["name"] == p],
            "usage": [{"file": u["file"], "line": u["line"], "text": src(u["file"], u["line"])}
                      for u in usage["usages"].get(p, [])[:6]],
            "indicators": [{"indicator": i["indicator"], "evidence": i["evidence"][:3]}
                           for i in indicators["indicators"] if i["package"] == p and i["severity"] != "info"],
        })
    return json.dumps({"python_requires": scan["python_requires"], "packages": facts}, indent=1)


def advise(model: str, prompt: str, retries=1) -> dict:
    """Returns {status: ok|blocked|invalid_response, ...}. Output is schema-validated."""
    info = model_info(model)
    if not info["available"]:
        return {"status": "blocked", "reason": info["blocker"], "model": info}
    err = None
    for _ in range(retries + 1):
        try:
            r = _post("/api/chat", {"model": model, "stream": False, "format": RESPONSE_SCHEMA,
                                    "options": {"temperature": 0, "num_predict": 3000},
                                    "messages": [{"role": "system", "content": SYSTEM},
                                                 {"role": "user", "content": prompt}]})
            data = parse_response(r["message"]["content"])
            return {"status": "ok", "model": info, "recommendations": data["recommendations"]}
        except (ValueError, jsonschema.ValidationError, KeyError) as e:
            err = f"{type(e).__name__}: {str(e)[:300]}"
        except (urllib.error.URLError, OSError) as e:
            return {"status": "blocked", "reason": f"inference request failed: {e}", "model": info}
    return {"status": "invalid_response", "reason": err, "model": info}


def parse_response(text: str) -> dict:
    data = json.loads(text)
    jsonschema.validate(data, RESPONSE_SCHEMA)
    return data
