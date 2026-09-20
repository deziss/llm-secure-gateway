"""
Regression tests guaranteeing the admin UI stays fully self-hosted.

The dashboard used to pull Tailwind, jQuery, DataTables, marked, highlight.js
and umap-js from public CDNs.  On an air-gapped or egress-filtered host that
left the UI unstyled and, because DataTables never loaded, completely unusable.
Every library is now vendored under ``static/``.

These tests fail the build if a CDN reference creeps back in.
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "llm_gateway"
TEMPLATES = SRC / "templates"
STATIC = SRC / "static"

# Matches src="..." / href="..." pointing at an absolute external URL.
EXTERNAL_ASSET_RE = re.compile(
    r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["'](https?:)?//[^"']+["']""",
    re.IGNORECASE | re.DOTALL,
)

# Matches local asset references we expect to resolve on disk.
LOCAL_ASSET_RE = re.compile(
    r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["'](/static/[^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def test_templates_exist():
    assert _templates(), f"no templates found under {TEMPLATES}"


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_no_external_script_or_stylesheet(template):
    """No template may load JS or CSS from an external origin."""
    matches = EXTERNAL_ASSET_RE.findall(template.read_text())
    assert not matches, (
        f"{template.relative_to(TEMPLATES)} loads an external asset. "
        "Vendor it under src/llm_gateway/static/ and reference it as /static/..."
    )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_no_cdn_document_write_fallback(template):
    """document.write() CDN fallbacks defeat the point of vendoring."""
    text = template.read_text()
    assert "document.write" not in text, (
        f"{template.relative_to(TEMPLATES)} contains a document.write() fallback"
    )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_local_assets_resolve(template):
    """Every /static/... reference must exist on disk."""
    for ref in LOCAL_ASSET_RE.findall(template.read_text()):
        # Strip cache-busting query strings and fragments (e.g. "?v=4").
        path = ref.split("?", 1)[0].split("#", 1)[0]
        target = STATIC / path[len("/static/"):]
        assert target.is_file(), (
            f"{template.relative_to(TEMPLATES)} references missing asset {ref}"
        )


def test_vendored_libraries_present():
    """The libraries the UI cannot function without are checked into the repo."""
    required = [
        "js/jquery.min.js",
        "js/jquery.dataTables.min.js",
        "js/chart.umd.min.js",
        "js/lucide.min.js",
        "js/purify.min.js",
        "js/marked.min.js",
        "js/highlight.min.js",
        "css/tailwind.min.css",
        "css/jquery.dataTables.min.css",
        "css/highlight-github-dark.min.css",
    ]
    missing = [name for name in required if not (STATIC / name).is_file()]
    assert not missing, f"vendored assets missing: {missing}"


def test_csp_allows_no_external_origins():
    """
    A 'self'-only script-src/style-src is what makes the fix permanent: a
    re-added CDN tag is blocked by the browser instead of quietly shipping.
    """
    source = (SRC / "middleware.py").read_text()
    csp_block = source.split('Content-Security-Policy"] = (', 1)[1].split(")", 1)[0]
    assert "http://" not in csp_block and "https://" not in csp_block, (
        "Content-Security-Policy still whitelists external origins:\n" + csp_block
    )


def test_tailwind_build_is_class_based_dark_mode():
    """
    The local Tailwind build must emit .dark rules, not prefers-color-scheme;
    the UI toggles themes by adding .dark to <html>.
    """
    css = (STATIC / "css" / "tailwind.min.css").read_text()
    assert "prefers-color-scheme" not in css, (
        "tailwind.min.css was built with darkMode:'media'; "
        "set darkMode:'class' in tailwind.config.js and re-run ./build-css.sh"
    )
    assert ".dark" in css, "tailwind.min.css contains no .dark variants"
