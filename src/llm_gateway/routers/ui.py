from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
import os

import pathlib

router = APIRouter(include_in_schema=False)
_TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Version token appended to project asset URLs (`/static/js/x.js?v=...`).
# Static files are served with an ETag but no Cache-Control, so browsers apply
# heuristic caching and can keep running JS/CSS from a previous release against
# freshly rendered (no-store) pages -- buttons and modals then silently break.
# Derived from the newest asset mtime at startup, so it changes on every deploy
# or restart without needing a manual bump.
def _asset_version() -> str:
    static = _TEMPLATE_DIR.parent / "static"
    try:
        newest = max(p.stat().st_mtime for p in static.rglob("*") if p.is_file())
    except ValueError:
        return "0"
    return format(int(newest), "x")


templates.env.globals["asset_v"] = _asset_version()

# Dev mode flag: drives the top-bar "insecure HTTP" badge and hides the
# login page's plain-HTTP warning (cookies already work without Secure).
from ..config import ALLOW_INSECURE_HTTP as _ALLOW_INSECURE_HTTP

templates.env.globals["allow_insecure_http"] = _ALLOW_INSECURE_HTTP

# Scopes offered in the Settings "Add custom rule" dropdown. Taken from the
# APIKeyScope enum so the list can't drift from what the policy engine accepts.
# Legacy flat aliases (chat, embeddings, ...) and the bare "*" are left out:
# new rules should use the hierarchical names.
from ..models import APIKeyScope as _APIKeyScope

_SCOPE_HELP = {
    "llm:chat": "Chat and text completions",
    "llm:embed": "Embeddings",
    "llm:read": "List models and running models",
    "llm:*": "Any LLM endpoint",
    "admin:*": "Admin API",
}
templates.env.globals["scope_options"] = [
    (s.value, _SCOPE_HELP.get(s.value, ""))
    for s in _APIKeyScope
    if ":" in s.value
]

# Base URL for API calls - defaults to empty string (relative URLs)
BASE_URL = os.getenv("BASE_URL", "")

from ..auth.users import fastapi_users
from ..auth.models import Role

# Dependency for UI (does not raise 401, returns None)
optional_current_user = fastapi_users.current_user(active=True, optional=True)

def get_template_context(user=None) -> dict:
    """Common context for all templates (excluding request — Starlette 1.0 handles it)."""
    user_role = None
    if user:
        user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
        user_role = user_role.lower()
    return {
        "user": user,
        "user_role": user_role,
        "base_url": BASE_URL
    }

@router.get("/auth/login")
async def login_page(request: Request, user=Depends(optional_current_user)):
    if user:
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "login.html", context=get_template_context())

@router.get("/auth/register")
async def register_page(request: Request, user=Depends(optional_current_user)):
    if user:
         return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "register.html", context=get_template_context())

@router.get("/auth/forgot-password")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", context=get_template_context())

@router.get("/auth/reset-password")
async def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset_password.html", context=get_template_context())

@router.get("/admin/dashboard")
async def dashboard_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse(request, "dashboard.html", context=get_template_context(user))

@router.get("/admin/view/servers")
async def backends_page(request: Request, user=Depends(optional_current_user)):
    if not user:
         return RedirectResponse("/auth/login")
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val not in [Role.ADMIN.value, Role.MANAGER.value]:
         return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "backends.html", context=get_template_context(user))

@router.get("/admin/view/backends")
async def backends_page_redirect():
    # Backends were renamed to model servers; keep old bookmarks working.
    return RedirectResponse("/admin/view/servers")

@router.get("/admin/view/projects")
async def owners_page(request: Request, user=Depends(optional_current_user)):
    if not user:
         return RedirectResponse("/auth/login")
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val not in [Role.ADMIN.value, Role.MANAGER.value, Role.DEVELOPER.value]:
         return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "owners.html", context=get_template_context(user))

@router.get("/admin/view/owners")
async def owners_page_redirect():
    # Owners were renamed to Projects; keep old bookmarks working.
    return RedirectResponse("/admin/view/projects")

@router.get("/admin/view/users")
async def users_page(request: Request, user=Depends(optional_current_user)):
    if not user:
         return RedirectResponse("/auth/login")
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val not in [Role.ADMIN.value, Role.MANAGER.value]:
         return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "users.html", context=get_template_context(user))

@router.get("/admin/view/playground")
async def playground_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse(request, "playground.html", context=get_template_context(user))

@router.get("/admin/view/playground/chat")
async def chat_playground_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse(request, "chat_playground.html", context=get_template_context(user))

@router.get("/admin/view/playground/embed")
async def embed_playground_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse(request, "embedding_playground.html", context=get_template_context(user))

@router.get("/admin/view/bots")
async def bots_page():
    # Bots moved to a tab on the Settings page; keep old links working.
    return RedirectResponse("/admin/view/settings#bots")

@router.get("/admin/view/spend")
async def spend_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val not in [Role.ADMIN.value, Role.MANAGER.value]:
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "spend.html", context=get_template_context(user))


@router.get("/admin/view/aliases")
async def aliases_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val not in [Role.ADMIN.value, Role.MANAGER.value]:
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "aliases.html", context=get_template_context(user))


@router.get("/admin/view/playground/compare")
async def compare_playground_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse(request, "playground_compare.html", context=get_template_context(user))


@router.get("/admin/view/settings")
async def settings_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val not in [Role.ADMIN.value, Role.MANAGER.value]:
         return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "settings.html", context=get_template_context(user))


@router.get("/admin/view/audit")
async def audit_page(request: Request, user=Depends(optional_current_user)):
    if not user:
        return RedirectResponse("/auth/login")
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val not in [Role.ADMIN.value, Role.MANAGER.value]:
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "audit.html", context=get_template_context(user))


@router.get("/")
async def root_redirect():
    return RedirectResponse(url="/admin/dashboard")
