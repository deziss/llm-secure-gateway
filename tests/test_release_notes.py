"""Tests for scripts/release_notes.py (release title + highlighted summary)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import release_notes as rn  # noqa: E402

MD = """## [1.2.0] - 2026-01-02

### 🚀 Big Feature

- detail line

### 🐛 Fixes

- fixed a thing

## [1.1.0] - 2026-01-01

### Older

- old
"""


def test_extract_section_stops_at_next_release():
    s = rn.extract_section(MD, "1.2.0")
    assert "Big Feature" in s and "Older" not in s
    assert rn.extract_section(MD, "9.9.9") == ""


def test_title_prefers_tag_message_then_headings():
    s = rn.extract_section(MD, "1.2.0")
    assert rn.build_title("1.2.0", s, "Release v1.2.0 - Widgets and Gizmos") == "v1.2.0 — Widgets and Gizmos"
    assert rn.build_title("1.2.0", s, "") == "v1.2.0 — Big Feature, Fixes"


def test_body_has_highlighted_summary_and_full_section():
    s = rn.extract_section(MD, "1.2.0")
    title = rn.build_title("1.2.0", s, "")
    body = rn.build_body("1.2.0", s, title)
    assert "**What's inside**" in body
    assert "- ✅ **Big Feature**" in body and "- ✅ **Fixes**" in body
    assert "detail line" in body          # full changelog section included
    assert "1.1.0" not in body            # older releases excluded
    assert "docker pull ghcr.io" in body


def test_theme_strips_emoji_and_phase_suffix():
    assert rn._theme_from_heading("🤖 Agent Intelligence (Phase 3)") == "Agent Intelligence"
    assert rn._theme_from_heading("Routing & Observability") == "Routing & Observability"


def test_cli_writes_files(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(MD, encoding="utf-8")
    t, b = tmp_path / "t.md", tmp_path / "b.md"
    rc = rn.main(["v1.2.0", "--repo", str(tmp_path), "--title-out", str(t), "--body-out", str(b)])
    assert rc == 0
    assert t.read_text().startswith("v1.2.0")
    assert "**What's inside**" in b.read_text()


def test_cli_errors_on_missing_section(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(MD, encoding="utf-8")
    assert rn.main(["v9.9.9", "--repo", str(tmp_path), "--body-out", str(tmp_path / "x")]) == 1
