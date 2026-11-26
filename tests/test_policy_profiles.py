"""Per-owner policy profiles: service semantics, admin CRUD, engine + proxy wiring."""
import pytest
from unittest.mock import MagicMock

from llm_gateway.services.policy_profiles import (
    CODE_MODEL_DENIED,
    CODE_MODEL_NOT_ALLOWED,
    evaluate_model_access,
    get_profile,
    invalidate_profile_cache,
    resolve_overrides,
)


@pytest.fixture(autouse=True)
def _clean_profile_cache():
    invalidate_profile_cache()
    yield
    invalidate_profile_cache()


# ---------------------------------------------------------------------------
# evaluate_model_access — wildcard matching matrix
# ---------------------------------------------------------------------------

class TestEvaluateModelAccess:
    def test_no_profile_allows(self):
        assert evaluate_model_access(None, "gpt-4") == (True, "")

    def test_no_model_allows(self):
        profile = {"denied_models": ["*"]}
        assert evaluate_model_access(profile, None) == (True, "")
        assert evaluate_model_access(profile, "") == (True, "")

    def test_exact_deny(self):
        profile = {"denied_models": ["gpt-4"], "allowed_models": None}
        assert evaluate_model_access(profile, "gpt-4") == (False, CODE_MODEL_DENIED)
        assert evaluate_model_access(profile, "gpt-3.5-turbo") == (True, "")

    def test_prefix_wildcard_deny(self):
        profile = {"denied_models": ["gpt-4*"], "allowed_models": None}
        assert evaluate_model_access(profile, "gpt-4-turbo") == (False, CODE_MODEL_DENIED)
        assert evaluate_model_access(profile, "gpt-3.5") == (True, "")

    def test_star_deny_all(self):
        profile = {"denied_models": ["*"], "allowed_models": None}
        assert evaluate_model_access(profile, "anything") == (False, CODE_MODEL_DENIED)

    def test_deny_wins_over_allow(self):
        profile = {"denied_models": ["gpt-4"], "allowed_models": ["*"]}
        assert evaluate_model_access(profile, "gpt-4") == (False, CODE_MODEL_DENIED)
        assert evaluate_model_access(profile, "mistral") == (True, "")

    def test_allowed_none_inherits_global(self):
        profile = {"denied_models": None, "allowed_models": None}
        assert evaluate_model_access(profile, "gpt-4") == (True, "")

    def test_allowed_exact(self):
        profile = {"allowed_models": ["llama3"]}
        assert evaluate_model_access(profile, "llama3") == (True, "")
        assert evaluate_model_access(profile, "gpt-4") == (False, CODE_MODEL_NOT_ALLOWED)

    def test_allowed_star(self):
        profile = {"allowed_models": ["*"]}
        assert evaluate_model_access(profile, "anything") == (True, "")

    def test_allowed_empty_list_denies(self):
        profile = {"allowed_models": []}
        assert evaluate_model_access(profile, "anything") == (False, CODE_MODEL_NOT_ALLOWED)

    def test_prefix_wildcard_not_honoured_on_allow_side(self):
        """Mirrors global allowed_models semantics: exact or '*' only."""
        profile = {"allowed_models": ["llama*"]}
        assert evaluate_model_access(profile, "llama3") == (False, CODE_MODEL_NOT_ALLOWED)

    def test_case_insensitive(self):
        profile = {"denied_models": ["GPT-4*"]}
        assert evaluate_model_access(profile, "gpt-4o") == (False, CODE_MODEL_DENIED)

    def test_accepts_orm_object_too(self):
        from llm_gateway.models import PolicyProfile

        p = PolicyProfile(owner_id="user:x", denied_models=["gpt-4"])
        assert evaluate_model_access(p, "gpt-4") == (False, CODE_MODEL_DENIED)


# ---------------------------------------------------------------------------
# resolve_overrides
# ---------------------------------------------------------------------------

class TestResolveOverrides:
    def test_inherits_globals_without_profile(self):
        result = resolve_overrides(None, {"enforce_guardrails": True, "pii_masking": False})
        assert result == {"enforce_guardrails": True, "pii_masking": False}

    def test_override_beats_global(self):
        profile = {
            "enforce_guardrails": False,
            "pii_masking": True,
            "daily_budget_usd": 25.0,
            "max_concurrent_requests": 5,
        }
        result = resolve_overrides(
            profile, {"enforce_guardrails": True, "pii_masking": False}
        )
        assert result["enforce_guardrails"] is False
        assert result["pii_masking"] is True
        assert result["daily_budget_usd"] == 25.0
        assert result["max_concurrent_requests"] == 5

    def test_none_fields_inherit(self):
        profile = {"enforce_guardrails": None, "pii_masking": None}
        result = resolve_overrides(profile, {"enforce_guardrails": True, "pii_masking": True})
        assert result == {"enforce_guardrails": True, "pii_masking": True}

    def test_budget_and_concurrency_echoed_only_when_set(self):
        result = resolve_overrides({"allowed_models": None}, {})
        assert "daily_budget_usd" not in result
        assert "max_concurrent_requests" not in result


# ---------------------------------------------------------------------------
# get_profile caching
# ---------------------------------------------------------------------------

class TestGetProfile:
    async def test_loads_and_caches(self, db_session):
        from llm_gateway.models import PolicyProfile

        db_session.add(PolicyProfile(owner_id="user:cache1", denied_models=["gpt-4"]))
        await db_session.commit()

        profile = await get_profile(db_session, "user:cache1")
        assert profile is not None
        assert profile["denied_models"] == ["gpt-4"]

        # Cached snapshot (dict) survives independent of the ORM session.
        assert isinstance(profile, dict)
        invalidate_profile_cache("user:cache1")

    async def test_negative_cache_and_invalidate(self, db_session):
        assert await get_profile(db_session, "user:none") is None

        from llm_gateway.models import PolicyProfile

        db_session.add(PolicyProfile(owner_id="user:none", denied_models=["*"]))
        await db_session.commit()

        # Still None: negative cache entry.
        assert await get_profile(db_session, "user:none") is None

        invalidate_profile_cache("user:none")
        profile = await get_profile(db_session, "user:none")
        assert profile is not None
        assert profile["denied_models"] == ["*"]

    async def test_missing_session_or_owner_returns_none(self):
        assert await get_profile(None, "user:x") is None
        assert await get_profile(MagicMock(), "") is None


# ---------------------------------------------------------------------------
# Admin CRUD endpoints
# ---------------------------------------------------------------------------

class TestAdminCrud:
    async def test_put_get_delete_roundtrip(self, client, db_session, admin_token_headers):
        from llm_gateway.models import Owner

        db_session.add(Owner(id="user:crud", type="user", name="CRUD"))
        await db_session.commit()

        body = {
            "allowed_models": ["llama3"],
            "denied_models": ["gpt-4*"],
            "enforce_guardrails": False,
            "description": "test profile",
        }
        r = await client.put("/admin/policy-profiles/user:crud", json=body, headers=admin_token_headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["owner_id"] == "user:crud"
        assert data["denied_models"] == ["gpt-4*"]
        assert data["enforce_guardrails"] is False

        r = await client.get("/admin/policy-profiles")
        assert r.status_code == 200
        profiles = r.json()["profiles"]
        assert [p["owner_id"] for p in profiles] == ["user:crud"]

        # Upsert again — update, not duplicate.
        r = await client.put(
            "/admin/policy-profiles/user:crud",
            json={**body, "max_concurrent_requests": 3},
            headers=admin_token_headers,
        )
        assert r.status_code == 200
        assert r.json()["max_concurrent_requests"] == 3
        assert r.json()["created_at"] is not None

        r = await client.get("/admin/policy-profiles")
        assert len(r.json()["profiles"]) == 1

        r = await client.delete("/admin/policy-profiles/user:crud", headers=admin_token_headers)
        assert r.status_code == 200
        assert r.json() == {"deleted": True, "owner_id": "user:crud"}

        r = await client.get("/admin/policy-profiles")
        assert r.json()["profiles"] == []

    async def test_put_unknown_owner_404(self, client, admin_token_headers):
        r = await client.put("/admin/policy-profiles/user:ghost", json={}, headers=admin_token_headers)
        assert r.status_code == 404

    async def test_delete_missing_profile_404(self, client, admin_token_headers):
        r = await client.delete("/admin/policy-profiles/user:nada", headers=admin_token_headers)
        assert r.status_code == 404

    async def test_writes_invalidate_cache(self, client, db_session, admin_token_headers):
        from llm_gateway.models import Owner, PolicyProfile

        db_session.add(Owner(id="user:inv", type="user", name="Inv"))
        db_session.add(PolicyProfile(owner_id="user:inv", denied_models=["old*"]))
        await db_session.commit()

        # Warm the cache.
        profile = await get_profile(db_session, "user:inv")
        assert profile["denied_models"] == ["old*"]

        await client.put(
            "/admin/policy-profiles/user:inv",
            json={"denied_models": ["new*"]},
            headers=admin_token_headers,
        )
        profile = await get_profile(db_session, "user:inv")
        assert profile["denied_models"] == ["new*"]


# ---------------------------------------------------------------------------
# PolicyEngine integration
# ---------------------------------------------------------------------------

class TestPolicyEngineIntegration:
    def _user(self, owner_id):
        user = MagicMock()
        user.scopes = ["llm:*"]
        user.owner_id = owner_id
        return user

    async def test_deny_via_denied_models(self, db_session):
        from llm_gateway.models import PolicyProfile
        from llm_gateway.policy import PolicyEngine, PolicyRequest

        db_session.add(PolicyProfile(owner_id="user:t1", denied_models=["gpt-4*"]))
        await db_session.commit()
        invalidate_profile_cache()

        req = PolicyRequest(
            user=self._user("user:t1"),
            action="invoke",
            resource="v1/chat/completions",
            context={"model": "gpt-4-turbo"},
        )
        engine = PolicyEngine()
        assert await engine.evaluate(req, session=db_session) is False
        assert req.context["policy_reason"] == CODE_MODEL_DENIED

    async def test_allow_when_profile_permits(self, db_session):
        from llm_gateway.models import PolicyProfile
        from llm_gateway.policy import PolicyEngine, PolicyRequest

        db_session.add(
            PolicyProfile(owner_id="user:t2", denied_models=["gpt-4*"], allowed_models=None)
        )
        await db_session.commit()
        invalidate_profile_cache()

        req = PolicyRequest(
            user=self._user("user:t2"),
            action="invoke",
            resource="v1/chat/completions",
            context={"model": "llama3"},
        )
        assert await PolicyEngine().evaluate(req, session=db_session) is True

    async def test_no_model_in_context_skips_profile(self, db_session):
        from llm_gateway.models import PolicyProfile
        from llm_gateway.policy import PolicyEngine, PolicyRequest

        db_session.add(PolicyProfile(owner_id="user:t3", denied_models=["*"]))
        await db_session.commit()
        invalidate_profile_cache()

        req = PolicyRequest(
            user=self._user("user:t3"),
            action="read",
            resource="admin/metrics",
            context={},
        )
        assert await PolicyEngine().evaluate(req, session=db_session) is True

    async def test_no_profile_allows(self, db_session):
        from llm_gateway.policy import PolicyEngine, PolicyRequest

        invalidate_profile_cache()
        req = PolicyRequest(
            user=self._user("user:no-profile"),
            action="invoke",
            resource="v1/chat/completions",
            context={"model": "gpt-4"},
        )
        assert await PolicyEngine().evaluate(req, session=db_session) is True

    async def test_magick_user_with_none_session_unaffected(self):
        """Existing callers pass session=None / non-str owner_id — must not break."""
        from llm_gateway.policy import PolicyEngine, PolicyRequest

        user = MagicMock()  # owner_id is a MagicMock, not a str
        user.scopes = ["llm:*"]
        req = PolicyRequest(
            user=user,
            action="invoke",
            resource="v1/chat/completions",
            context={"model": "gpt-4"},
        )
        assert await PolicyEngine().evaluate(req, session=None) is True

    async def test_broken_session_fails_open(self, caplog):
        """DB errors in the profile hook must never raise out of evaluate()."""
        from llm_gateway.policy import PolicyEngine, PolicyRequest

        session = MagicMock()
        session.execute = MagicMock(side_effect=RuntimeError("db down"))
        req = PolicyRequest(
            user=self._user("user:boom"),
            action="invoke",
            resource="v1/chat/completions",
            context={"model": "gpt-4"},
        )
        assert await PolicyEngine().evaluate(req, session=session) is True


# ---------------------------------------------------------------------------
# Proxy hook: check_owner_permissions
# ---------------------------------------------------------------------------

class TestProxyHook:
    def _make_backend(self):
        backend = MagicMock()
        backend.name = "test-backend"
        backend.allowed_endpoints = ["v1/chat/completions"]
        return backend

    async def test_profile_denial_raises_structured_403(self, db_session):
        from fastapi import HTTPException

        from llm_gateway.models import Owner, OwnerPermission, PolicyProfile
        from llm_gateway.proxy_helpers import check_owner_permissions

        db_session.add(Owner(id="user:hook", type="user", name="Hook"))
        db_session.add(
            OwnerPermission(owner_id="user:hook", backend_name="test-backend", allowed_models=["*"])
        )
        db_session.add(PolicyProfile(owner_id="user:hook", denied_models=["gpt-4*"]))
        await db_session.commit()
        invalidate_profile_cache()

        with pytest.raises(HTTPException) as exc:
            await check_owner_permissions(
                db_session, "user:hook", self._make_backend(), "v1/chat/completions", "gpt-4o"
            )
        assert exc.value.status_code == 403
        err = exc.value.detail["error"]
        assert err["code"] == "model_not_allowed_by_policy_profile"
        assert err["param"] == "gpt-4o"

    async def test_no_profile_allows(self, db_session):
        from llm_gateway.models import Owner, OwnerPermission
        from llm_gateway.proxy_helpers import check_owner_permissions

        db_session.add(Owner(id="user:hook2", type="user", name="Hook2"))
        db_session.add(
            OwnerPermission(owner_id="user:hook2", backend_name="test-backend", allowed_models=["*"])
        )
        await db_session.commit()
        invalidate_profile_cache()

        await check_owner_permissions(
            db_session, "user:hook2", self._make_backend(), "v1/chat/completions", "gpt-4o"
        )
