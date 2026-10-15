"""Real local inference. Skipped unless a local Ollama serves gemma4:e2b (run: pytest depguard/tests/test_live_gemma.py)."""
import json

import pytest

from depguard import advisor

MODEL = "gemma4:e2b"
pytestmark = pytest.mark.skipif(not advisor.model_info(MODEL)["available"], reason=f"{MODEL} not available locally")


def test_live_gemma_returns_schema_valid_recommendation():
    prompt = json.dumps({"python_requires": ">=3.10", "packages": [{
        "package": "jinja2",
        "declared": [{"file": "requirements.txt", "line": 4, "text": "Jinja2==2.11.3"}],
        "usage": [], "indicators": [{"indicator": "observed_in_baseline_output", "evidence": [
            "E   ImportError: cannot import name 'soft_unicode' from 'markupsafe'"]}]}]})
    r = advisor.advise(MODEL, prompt)
    assert r["status"] == "ok", r
    assert r["model"]["family"] == "gemma4"
    assert r["recommendations"][0]["package"].lower() == "jinja2"
