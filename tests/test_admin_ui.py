"""
Regression tests for the admin UI shell and the invariants the redesign set up.

Pure static/Jinja checks -- no browser. They guard things that were broken
before and are cheap to break again:

* role gating of the sidebar (it replaced header.html, which owned this),
* every page shares the one shell,
* no inline `onclick` interpolating data (an injection hole: the browser
  HTML-decodes the attribute *before* JS parses it, so escapeHtml() is not
  enough inside an inline handler),
* modals are named, and the responsive-table CSS survives DataTables' inline
  width.
"""
import re
from pathlib import Path

import jinja2
import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "llm_gateway"
TEMPLATES = SRC / "templates"
JS = SRC / "static" / "js"
CSS = (SRC / "static" / "css" / "tailwind.min.css").read_text()

AUTH_PAGES = {"login", "register", "forgot_password", "reset_password"}
ADMIN_PAGES = [
    "audit", "dashboard", "backends", "owners", "users", "spend", "aliases",
    "settings", "playground", "chat_playground", "embedding_playground",
    "playground_compare",
]

_env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)


class _User:
    email = "admin@example.com"


def _render(name, role="admin", authed=True):
    return _env.get_template(f"{name}.html").render(
        user=_User() if authed else None, user_role=role if authed else "", base_url=""
    )


def _nav_links(html):
    return re.findall(r'<a href="(/admin/[^"]+)" title=', html)


# ── Sidebar role gating (mirrors the old header.html exactly) ──────────────

@pytest.mark.parametrize(
    "role,expected_count,must_have,must_lack",
    [
        ("admin", 12, ["/admin/view/settings", "/admin/view/audit", "/admin/view/servers"], []),
        ("manager", 12, ["/admin/view/settings", "/admin/view/audit", "/admin/view/users"], []),
        ("developer", 6, ["/admin/view/projects", "/admin/dashboard"],
         ["/admin/view/servers", "/admin/view/users", "/admin/view/settings", "/admin/view/spend", "/admin/view/audit"]),
        ("viewer", 5, ["/admin/dashboard", "/admin/view/playground"],
         ["/admin/view/projects", "/admin/view/servers", "/admin/view/settings"]),
    ],
)
def test_sidebar_role_gating(role, expected_count, must_have, must_lack):
    links = _nav_links(_render("dashboard", role))
    assert len(links) == expected_count, f"{role}: {links}"
    for href in must_have:
        assert href in links
    for href in must_lack:
        assert href not in links, f"{role} must not see {href}"


# ── One shell for every page ───────────────────────────────────────────────

@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_admin_pages_use_the_shell(page):
    html = _render(page)
    assert 'id="sidebar"' in html and 'id="sidebarBackdrop"' in html
    assert '<main id="main"' in html, "pages must render inside <main>"
    assert "Skip to content" in html, "skip link missing"
    assert "nav-header" not in html, "old header.html markup resurfaced"
    assert "/static/js/shell.js" in html


@pytest.mark.parametrize("page", sorted(AUTH_PAGES))
def test_auth_pages_have_no_shell(page):
    html = _render(page, authed=False)
    assert 'id="sidebar"' not in html
    assert '<main id="main"' in html
    assert "/static/js/shell.js" not in html


def test_old_header_template_is_gone():
    assert not (TEMPLATES / "header.html").exists()


# ── Injection class: no data inside inline handlers ────────────────────────

# onclick="..." (or onclick='...') whose value contains a template-literal
# interpolation. Static handlers like onclick="closeAddModal()" are fine.
_INLINE_HANDLER_WITH_DATA = re.compile(r"""on(?:click|change|input|submit)\s*=\s*(["'])[^"']*\$\{""")


def _inline_script_text(html):
    return "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S))


@pytest.mark.parametrize(
    "path",
    sorted(p for p in JS.glob("*.js") if not p.name.endswith(".min.js")),
    ids=lambda p: p.name,
)
def test_no_inline_handlers_with_interpolated_data_in_js(path):
    hits = _INLINE_HANDLER_WITH_DATA.findall(path.read_text())
    assert not hits, (
        f"{path.name} builds an inline event handler from data. Use "
        'data-action="..." data-id="${escapeHtml(id)}" and a delegated listener; '
        "escapeHtml() does not make a value safe inside an inline JS string."
    )


@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_no_inline_handlers_with_interpolated_data_in_templates(page):
    src = (TEMPLATES / f"{page}.html").read_text()
    assert not _INLINE_HANDLER_WITH_DATA.search(_inline_script_text(src)), (
        f"{page}.html builds an inline event handler from data in its <script>"
    )


def test_escape_html_escapes_quotes():
    """It is used inside quoted attribute values, so quotes must be escaped."""
    src = (JS / "ui-components.js").read_text()
    fn = re.search(r"function escapeHtml\(.*?\n}", src, re.S).group(0)
    for needle in ('&quot;', '&#39;'):
        assert needle in fn, f"escapeHtml no longer produces {needle}"


# ── Modals are named ───────────────────────────────────────────────────────

@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_every_dialog_has_a_name(page):
    """role=dialog needs an accessible name: aria-label(ledby) or a heading
    admin-modal.js can wire up at runtime."""
    html = _render(page)
    for m in re.finditer(r'<div[^>]*role="dialog"[^>]*>', html):
        tag = m.group(0)
        if "aria-label" in tag:
            continue
        # find the dialog's body: up to the next top-level closing is hard to
        # parse; settle for "a heading appears somewhere after the opening tag
        # before the next role=dialog".
        rest = html[m.end():]
        nxt = re.search(r'role="dialog"', rest)
        body = rest[: nxt.start()] if nxt else rest
        assert re.search(r"<h[1-4]\b", body), f"{page}: dialog without any heading: {tag[:90]}"


# ── Form labels ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", ["settings", "aliases", "owners", "playground"])
def test_label_for_targets_exist(page):
    html = _render(page)
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    missing = [f for f in re.findall(r'<label[^>]*\bfor="([^"]+)"', html) if f not in ids]
    assert not missing, f"{page}: <label for> points at missing ids: {missing}"


# ── CSS artefact ───────────────────────────────────────────────────────────

def test_token_utilities_are_built():
    for cls in ("bg-surface-raised", "text-ink-muted", "border-hairline", "min-h-touch", "rounded-panel"):
        assert cls in CSS, f"{cls} missing from tailwind.min.css; run ./build-css.sh"


def test_responsive_table_css_survives_datatables_inline_width():
    """DataTables writes style="width:643px" onto the table; only !important
    beats an inline style, otherwise cards stay 643px wide on a phone."""
    assert re.search(r"table\.responsive-table\{width:100%!important\}", CSS.replace(" ", "")), (
        "mobile table width override missing or lost its !important"
    )


def test_every_page_script_is_loaded_by_base():
    base = (TEMPLATES / "base.html").read_text()
    for js in ("admin-modal.js", "admin-table.js", "shell.js"):
        assert js in base, f"base.html does not load {js}"
        assert (JS / js).is_file()


# ── Settings tabs ──────────────────────────────────────────────────────────

def test_settings_tabs_follow_role():
    """Admin-only sections stay out of the page for managers, and so do their tabs."""
    admin = _render("settings", "admin")
    manager = _render("settings", "manager")
    assert re.findall(r'data-tab="(\w+)"', admin) == ["general", "access", "routing", "performance", "compliance", "scope", "bots"]
    assert re.findall(r'data-tab="(\w+)"', manager) == ["general", "access", "bots"]
    # every tab has at least one section, and every section belongs to a tab
    for html in (admin, manager):
        tabs = set(re.findall(r'data-tab="(\w+)"', html))
        panels = set(re.findall(r'data-settings-tab="(\w+)"', html))
        assert tabs == panels, (tabs, panels)
    assert 'id="settingsScope"' in admin and 'id="settingsScope"' not in manager
