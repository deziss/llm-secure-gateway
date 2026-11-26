#!/usr/bin/env python3
"""Generate a GitHub Release title + body for a version tag.

Reads CHANGELOG.md, extracts the section for the tag version, and builds:

- TITLE: "vX.Y.Z — <one-line themes>" from the annotated tag message when
  available ("Release vX.Y.Z - a, b, c"), else from the section's ### headings.
- BODY: a short highlighted **"What's inside"** bullet summary (one bullet per
  feature area, from the section's ### headings), followed by the full
  changelog section.

Usage:
    python3 scripts/release_notes.py v0.17.0 [--title-out T --body-out B]
                                             [--print]
Exit 1 when the changelog has no section for the version.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "CHANGELOG.md"
MAX_HIGHLIGHTS = 8
MAX_HIGHLIGHT_CHARS = 140


def _repo_rel(repo_root: Path | None) -> Path:
    """CHANGELOG path; tests pass --repo, default is the real repo root."""
    return (repo_root or REPO) / "CHANGELOG.md"


def extract_section(md: str, version: str) -> str:
    """Markdown between '## [X.Y.Z]' and the next '## [' (or EOF)."""
    pat = re.compile(rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)",
                     re.MULTILINE | re.DOTALL)
    m = pat.search(md)
    return m.group(1).strip() if m else ""


def headings_of(section: str) -> list[str]:
    return [h.strip() for h in re.findall(r"^###\s+(.+)$", section, re.MULTILINE)]


def tag_message(tag: str, repo_root: Path | None = None) -> str:
    """The annotated tag message (first line), empty if tag missing/lightweight."""
    try:
        out = subprocess.run(
            ["git", "tag", "-l", "--format=%(contents)", tag],
            cwd=str(repo_root or REPO), capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    except Exception:
        return ""


def _theme_from_heading(heading: str) -> str:
    """'🤖 Agent Intelligence (Phase 3)' -> 'Agent Intelligence'."""
    h = re.sub(r"^[^\w]+", "", heading)            # drop leading emoji/symbol chars
    h = re.sub(r"\s*\((Phase|Release)[^)]*\)\s*$", "", h, flags=re.IGNORECASE)
    return h.strip(" —-")


def build_title(version: str, section: str, msg: str) -> str:
    themes = [h for h in (_theme_from_heading(x) for x in headings_of(section)) if h]
    themes_txt = ", ".join(themes[:3]) if themes else ""
    m = re.match(rf"^\s*Release\s+{re.escape('v' + version)}\s*[-–—:]\s*(.+)$", msg, re.IGNORECASE)
    if m:
        themes_txt = m.group(1).strip() or themes_txt
    return f"v{version} — {themes_txt}" if themes_txt else f"v{version}"


def build_body(version: str, section: str, title: str) -> str:
    heads = headings_of(section)
    lines = [f"# {title}", ""]
    if heads:
        lines.append("**What's inside**")
        lines.append("")
        for h in heads[:MAX_HIGHLIGHTS]:
            theme = _theme_from_heading(h)
            lines.append(f"- ✅ **{theme}**")
        if len(heads) > MAX_HIGHLIGHTS:
            lines.append(f"- ✅ …and {len(heads) - MAX_HIGHLIGHTS} more areas — see details below")
        lines += ["", "---", ""]
    lines.append(section)
    lines += ["", "## Installation", "",
              "```bash",
              f"docker pull ghcr.io/deziss/llm-secure-gateway:v{version}",
              "```", "",
              f"See `UPGRADE.md` for migration notes; release details are in `CHANGELOG.md`."]
    body = "\n".join(lines)
    # Safety: keep individual highlight lines from exploding (long headings).
    return "\n".join(l if len(l) <= MAX_HIGHLIGHT_CHARS * 4 else l[:MAX_HIGHLIGHT_CHARS * 4] + "…"
                     for l in body.splitlines())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tag", nargs="?", help="tag name, e.g. v0.17.0")
    ap.add_argument("--latest", action="store_true",
                    help="use the first (newest) version section of CHANGELOG.md")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--title-out", type=Path, default=None)
    ap.add_argument("--body-out", type=Path, default=None)
    ap.add_argument("--print", dest="do_print", action="store_true")
    args = ap.parse_args(argv)

    if args.latest or not args.tag:
        md_text = _repo_rel(args.repo).read_text(encoding="utf-8")
        first = re.search(r"^## \[(\d[^\]]+)\]", md_text, re.MULTILINE)
        if not first:
            print("error: no version sections found in CHANGELOG.md", file=sys.stderr)
            return 1
        tag = "v" + first.group(1)
    else:
        tag = args.tag.strip()
    m = re.match(r"^v(\d+\.\d+\.\d+(?:[-.][\w.]+)?)$", tag)
    if not m:
        print(f"error: tag {tag!r} is not of the form vX.Y.Z", file=sys.stderr)
        return 2
    version = m.group(1)

    path = _repo_rel(args.repo)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    section = extract_section(path.read_text(encoding="utf-8"), version)
    if not section:
        print(f"error: CHANGELOG.md has no section for {version}", file=sys.stderr)
        return 1

    title = build_title(version, section, tag_message(tag, args.repo))
    body = build_body(version, section, title)
    if args.title_out:
        args.title_out.write_text(title, encoding="utf-8")
    if args.body_out:
        args.body_out.write_text(body, encoding="utf-8")
    if args.do_print or not (args.title_out or args.body_out):
        print("TITLE:", title)
        print()
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
