"""Role-based access: the user-edit privilege escalation, read-only viewers,
and which guard each admin route uses."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from llm_gateway.auth.models import Role
from llm_gateway.routers.owners import require_writer
from llm_gateway.routers.users import UserUpdate, _check_user_update_allowed


def _u(role, superuser=False, uid=None):
    return SimpleNamespace(id=uid or uuid.uuid4(), role=role, is_superuser=superuser)


ADMIN, MANAGER, DEV, VIEWER = Role.ADMIN, Role.MANAGER, Role.DEVELOPER, Role.VIEWER


def _allowed(actor, target, **upd):
    try:
        _check_user_update_allowed(actor, target, UserUpdate(**upd))
        return True
    except HTTPException as e:
        assert e.status_code == 403
        return False


# ── PATCH /admin/users/{id} ───────────────────────────────────────────────

def test_manager_cannot_promote_anyone_to_admin_or_manager():
    m = _u(MANAGER)
    assert not _allowed(m, _u(DEV), role=ADMIN)
    assert not _allowed(m, _u(VIEWER), role=MANAGER)


def test_manager_cannot_grant_superuser():
    assert not _allowed(_u(MANAGER), _u(DEV), is_superuser=True)


def test_manager_cannot_touch_admin_superuser_or_manager_accounts():
    m = _u(MANAGER)
    assert not _allowed(m, _u(ADMIN), is_active=False)
    assert not _allowed(m, _u(DEV, superuser=True), is_active=False)
    assert not _allowed(m, _u(MANAGER), role=VIEWER)


def test_manager_can_manage_developers_and_viewers():
    m = _u(MANAGER)
    assert _allowed(m, _u(VIEWER), role=DEV)
    assert _allowed(m, _u(DEV), role=VIEWER)
    assert _allowed(m, _u(DEV), is_active=False)


def test_nobody_changes_their_own_role_or_state():
    me = uuid.uuid4()
    assert not _allowed(_u(MANAGER, uid=me), _u(MANAGER, uid=me), role=ADMIN)
    assert not _allowed(_u(ADMIN, uid=me), _u(ADMIN, uid=me), is_active=False)
    assert not _allowed(_u(ADMIN, uid=me), _u(ADMIN, uid=me), role=VIEWER)


def test_admin_and_superuser_can_change_roles():
    assert _allowed(_u(ADMIN), _u(DEV), role=ADMIN)
    assert _allowed(_u(ADMIN), _u(MANAGER), is_superuser=True)
    assert _allowed(_u(VIEWER, superuser=True), _u(ADMIN), role=MANAGER)


# ── Owners / keys writes ──────────────────────────────────────────────────

def test_viewer_is_read_only_on_owners():
    with pytest.raises(HTTPException) as e:
        require_writer(_u(VIEWER))
    assert e.value.status_code == 403
    for role in (ADMIN, MANAGER, DEV):
        assert require_writer(_u(role)).role == role
    assert require_writer(_u(VIEWER, superuser=True))


# ── Route guards ──────────────────────────────────────────────────────────

def _guards():
    from llm_gateway.main import app

    def names(dep):
        out = set()
        for d in dep.dependencies:
            out.add(getattr(d.call, "__name__", ""))
            out |= names(d)
        return out

    table = {}
    for r in app.routes:
        if getattr(r, "dependant", None) is None:
            continue
        for m in r.methods or ():
            table[(m, r.path)] = names(r.dependant)
    return table


@pytest.mark.parametrize("method,path,guard", [
    ("GET", "/admin/metrics", "require_manager"),
    ("GET", "/admin/metrics/stream", "require_manager"),
    ("GET", "/admin/spend/summary", "require_manager"),
    ("GET", "/admin/spend/by-model", "require_manager"),
    ("GET", "/admin/bots", "require_manager"),
    ("POST", "/admin/bots", "require_manager"),
    ("PATCH", "/admin/backends/{name}", "require_manager"),
    ("DELETE", "/admin/backends/{name}", "require_manager"),
    ("POST", "/admin/backends/{name}/sync-models", "require_manager"),
    ("GET", "/admin/backends/{name}/health", "require_manager"),
    # Changes on the backend host, server settings and user lifecycle stay admin-only.
    ("POST", "/admin/backends/{name}/pull", "require_admin"),
    ("DELETE", "/admin/backends/{name}/models/{model_name:path}", "require_admin"),
    ("GET", "/admin/settings", "require_admin"),
    ("POST", "/admin/users", "require_admin"),
    ("DELETE", "/admin/users/{user_id}", "require_admin"),
    ("POST", "/admin/owners/{owner_id}/permissions", "require_admin"),
    ("POST", "/admin/owners", "require_writer"),
    ("POST", "/admin/owners/{owner_id}/keys", "require_writer"),
])
def test_route_guard(method, path, guard):
    g = _guards()
    assert (method, path) in g, f"route {method} {path} not found"
    assert guard in g[(method, path)], f"{method} {path}: {sorted(g[(method, path)])}"


# ── Owners → Projects rename ──────────────────────────────────────────────

def test_projects_api_mirrors_owners_with_same_guards():
    g = _guards()
    owners = {(m, p) for (m, p) in g if p.startswith("/admin/owners")}
    assert owners, "no /admin/owners routes found"
    for m, p in owners:
        alias = (m, p.replace("/admin/owners", "/admin/projects", 1))
        assert alias in g, f"missing alias {alias}"
        assert g[alias] == g[(m, p)], f"guards differ for {alias}"


def test_owners_routes_are_deprecated_in_openapi():
    from llm_gateway.main import app
    spec = app.openapi()["paths"]
    assert spec["/admin/owners"]["get"].get("deprecated") is True
    assert not spec["/admin/projects"]["get"].get("deprecated")


def test_owners_page_redirects_to_projects():
    from llm_gateway.main import app
    paths = {r.path for r in app.routes}
    assert "/admin/view/projects" in paths and "/admin/view/owners" in paths


# ── Backends → Model servers rename ───────────────────────────────────────

def test_servers_api_mirrors_backends_with_same_guards():
    g = _guards()
    backends = {(m, p) for (m, p) in g if p.startswith("/admin/backends")}
    assert backends
    for m, p in backends:
        alias = (m, p.replace("/admin/backends", "/admin/servers", 1))
        assert alias in g, f"missing alias {alias}"
        assert g[alias] == g[(m, p)], f"guards differ for {alias}"


def test_model_server_accepts_and_emits_server_type():
    from llm_gateway.models import LLMBackend, ModelServer
    assert LLMBackend is ModelServer and ModelServer.__tablename__ == "model_server"
    m = ModelServer.model_validate({"name": "a", "base_url": "http://x", "server_type": "vllm"})
    d = m.model_dump(mode="json")
    assert d["backend_type"] == "vllm" and d["server_type"] == "vllm"
    # explicit backend_type wins over server_type
    m2 = ModelServer.model_validate({"name": "b", "base_url": "http://x", "backend_type": "ollama", "server_type": "vllm"})
    assert m2.model_dump(mode="json")["server_type"] == "ollama"


def _run_migration_005(setup_sql):
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "005_rename_backend_to_model_server.py"
    spec = importlib.util.spec_from_file_location("m005", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for stmt in setup_sql:
            conn.exec_driver_sql(stmt)
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        tables = set(sa.inspect(conn).get_table_names())
        rows = conn.exec_driver_sql("SELECT count(*) FROM model_server").scalar() if "model_server" in tables else None
    return tables, rows


def test_migration_005_renames_table_and_keeps_rows():
    tables, rows = _run_migration_005([
        "CREATE TABLE llmbackend (name TEXT PRIMARY KEY)",
        "INSERT INTO llmbackend VALUES ('a'), ('b')",
    ])
    assert "llmbackend" not in tables and rows == 2


def test_migration_005_drops_empty_duplicate_from_create_all():
    tables, rows = _run_migration_005([
        "CREATE TABLE llmbackend (name TEXT PRIMARY KEY)",
        "INSERT INTO llmbackend VALUES ('a')",
        "CREATE TABLE model_server (name TEXT PRIMARY KEY)",
    ])
    assert "llmbackend" not in tables and rows == 1


def test_migration_005_refuses_two_populated_tables():
    with pytest.raises(RuntimeError):
        _run_migration_005([
            "CREATE TABLE llmbackend (name TEXT PRIMARY KEY)",
            "INSERT INTO llmbackend VALUES ('a')",
            "CREATE TABLE model_server (name TEXT PRIMARY KEY)",
            "INSERT INTO model_server VALUES ('z')",
        ])
