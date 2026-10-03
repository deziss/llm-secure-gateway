"""
Static guard against references to names that don't exist.

/direct/{backend}/... returned HTTP 500 on every request from v0.6.0 until
v0.11.2: the handler passed `fallback_chain_id` (and used `cache_key`,
`enable_semantic_cache`) without ever defining them. Tests never exercised that
route, so the NameError only surfaced at runtime for users. pyflakes finds this
class of bug without running anything.
"""
from pathlib import Path

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
pyflakes_reporter = pytest.importorskip("pyflakes.reporter")

SRC = Path(__file__).resolve().parents[1] / "src" / "llm_gateway"


class _Collect:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        pass


def test_no_undefined_names_in_package():
    out = _Collect()
    pyflakes_api.checkRecursive([str(SRC)], pyflakes_reporter.Reporter(out, out))
    undefined = [l.strip() for l in "".join(out.lines).splitlines() if "undefined name" in l]
    assert not undefined, "References to undefined names (would crash at runtime):\n  " + "\n  ".join(undefined)
