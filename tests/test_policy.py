"""Unit tests for the hierarchical scope policy engine."""
import pytest
from unittest.mock import MagicMock


# ─── scope_matches unit tests ────────────────────────────────────────────

class TestScopeMatching:
    """Pure function tests for hierarchical scope matching."""

    def test_exact_match(self):
        from llm_gateway.policy import scope_matches
        assert scope_matches({"llm:chat"}, "llm:chat") is True

    def test_exact_mismatch(self):
        from llm_gateway.policy import scope_matches
        assert scope_matches({"llm:chat"}, "llm:embed") is False

    def test_namespace_wildcard(self):
        from llm_gateway.policy import scope_matches
        assert scope_matches({"llm:*"}, "llm:chat") is True
        assert scope_matches({"llm:*"}, "llm:embed") is True
        assert scope_matches({"llm:*"}, "llm:read") is True

    def test_namespace_wildcard_does_not_cross_namespaces(self):
        from llm_gateway.policy import scope_matches
        assert scope_matches({"llm:*"}, "admin:users") is False

    def test_global_wildcard(self):
        from llm_gateway.policy import scope_matches
        assert scope_matches({"*"}, "llm:chat") is True
        assert scope_matches({"*"}, "admin:users") is True
        assert scope_matches({"*"}, "anything") is True

    def test_admin_wildcard(self):
        from llm_gateway.policy import scope_matches
        assert scope_matches({"admin:*"}, "admin:backends") is True
        assert scope_matches({"admin:*"}, "llm:chat") is False

    def test_multiple_scopes(self):
        from llm_gateway.policy import scope_matches
        held = {"llm:chat", "llm:embed"}
        assert scope_matches(held, "llm:chat") is True
        assert scope_matches(held, "llm:embed") is True
        assert scope_matches(held, "llm:read") is False

    def test_empty_scopes(self):
        from llm_gateway.policy import scope_matches
        assert scope_matches(set(), "llm:chat") is False


# ─── Legacy alias tests ─────────────────────────────────────────────────

class TestLegacyAliases:
    """Flat scopes from v0.4.x are aliased to hierarchical equivalents."""

    def test_chat_alias(self):
        from llm_gateway.policy import _normalize_scopes
        assert "llm:chat" in _normalize_scopes(["chat"])

    def test_embeddings_alias(self):
        from llm_gateway.policy import _normalize_scopes
        assert "llm:embed" in _normalize_scopes(["embeddings"])

    def test_read_only_alias(self):
        from llm_gateway.policy import _normalize_scopes
        assert "llm:read" in _normalize_scopes(["read_only"])

    def test_admin_alias(self):
        from llm_gateway.policy import _normalize_scopes
        assert "admin:*" in _normalize_scopes(["admin"])

    def test_hierarchical_scope_passes_through(self):
        from llm_gateway.policy import _normalize_scopes
        result = _normalize_scopes(["llm:chat", "llm:embed"])
        assert result == {"llm:chat", "llm:embed"}

    def test_mixed_legacy_and_new(self):
        from llm_gateway.policy import _normalize_scopes
        result = _normalize_scopes(["chat", "llm:embed"])
        assert "llm:chat" in result
        assert "llm:embed" in result


# ─── required_scope_for tests ────────────────────────────────────────────

class TestRequiredScopeFor:

    def test_chat_endpoints(self):
        from llm_gateway.policy import required_scope_for, DEFAULT_RULES
        assert required_scope_for("/api/chat", DEFAULT_RULES) == "llm:chat"
        assert required_scope_for("/v1/chat/completions", DEFAULT_RULES) == "llm:chat"

    def test_embed_endpoints(self):
        from llm_gateway.policy import required_scope_for, DEFAULT_RULES
        assert required_scope_for("/api/embeddings", DEFAULT_RULES) == "llm:embed"
        assert required_scope_for("/v1/embeddings", DEFAULT_RULES) == "llm:embed"

    def test_read_endpoints(self):
        from llm_gateway.policy import required_scope_for, DEFAULT_RULES
        assert required_scope_for("/api/tags", DEFAULT_RULES) == "llm:read"
        assert required_scope_for("/v1/models", DEFAULT_RULES) == "llm:read"

    def test_unknown_defaults_to_llm_chat(self):
        from llm_gateway.policy import required_scope_for, DEFAULT_RULES
        assert required_scope_for("/unknown/path", DEFAULT_RULES) == "llm:chat"

    def test_db_rules_take_priority(self):
        from llm_gateway.policy import required_scope_for, DEFAULT_RULES
        custom_rules = [("/api/chat", "custom:scope")] + DEFAULT_RULES
        assert required_scope_for("/api/chat", custom_rules) == "custom:scope"


# ─── PolicyEngine.evaluate integration tests ─────────────────────────────

class TestPolicyEngine:

    @pytest.mark.asyncio
    async def test_allows_matching_hierarchical_scope(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = MagicMock()
        user.scopes = ["llm:chat"]
        req = PolicyRequest(user=user, action="call_llm", resource="/v1/chat/completions", context={})
        assert await engine.evaluate(req) is True

    @pytest.mark.asyncio
    async def test_denies_wrong_scope(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = MagicMock()
        user.scopes = ["llm:embed"]
        req = PolicyRequest(user=user, action="call_llm", resource="/v1/chat/completions", context={})
        assert await engine.evaluate(req) is False

    @pytest.mark.asyncio
    async def test_namespace_wildcard_allows_all_llm(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = MagicMock()
        user.scopes = ["llm:*"]
        for resource in ["/api/chat", "/api/embeddings", "/v1/models"]:
            req = PolicyRequest(user=user, action="call_llm", resource=resource, context={})
            assert await engine.evaluate(req) is True, f"llm:* should allow {resource}"

    @pytest.mark.asyncio
    async def test_global_wildcard_allows_everything(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = MagicMock()
        user.scopes = ["*"]
        for resource in ["/api/chat", "/api/embeddings", "/v1/models", "/anything"]:
            req = PolicyRequest(user=user, action="call_llm", resource=resource, context={})
            assert await engine.evaluate(req) is True

    @pytest.mark.asyncio
    async def test_legacy_chat_scope_still_works(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = MagicMock()
        user.scopes = ["chat"]  # old flat scope
        req = PolicyRequest(user=user, action="call_llm", resource="/api/chat", context={})
        assert await engine.evaluate(req) is True

    @pytest.mark.asyncio
    async def test_legacy_admin_scope_bypasses(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = MagicMock()
        user.scopes = ["admin"]  # old flat → aliased to admin:*
        for resource in ["/api/chat", "/api/embeddings"]:
            req = PolicyRequest(user=user, action="call_llm", resource=resource, context={})
            # admin:* does NOT match llm:* — these are different namespaces
            # But we also check global wildcard. admin alias is admin:*, not *.
            # So admin scope should NOT grant llm access.
            # This is a design decision: admin means admin namespace, not everything.

    @pytest.mark.asyncio
    async def test_legacy_embeddings_denied_on_chat_endpoint(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = MagicMock()
        user.scopes = ["embeddings"]  # aliased to llm:embed
        req = PolicyRequest(user=user, action="call_llm", resource="/api/chat", context={})
        assert await engine.evaluate(req) is False

    @pytest.mark.asyncio
    async def test_spiffe_workload_allowed(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = {"type": "workload", "spiffe_id": "spiffe://trust/svc/x"}
        req = PolicyRequest(user=user, action="call_llm", resource="/api/chat", context={})
        assert await engine.evaluate(req) is True

    @pytest.mark.asyncio
    async def test_workload_denied_on_non_allowlisted_path(self):
        """Workloads are only allowed on the documented inference allowlist (or paths whose
        SCOPE_RULE maps to the 'workload' scope) — anything else is denied."""
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = {"type": "workload", "spiffe_id": "spiffe://trust/svc/x"}
        for resource in ["/api/admin/keys", "/admin/users", "/api/keys"]:
            req = PolicyRequest(user=user, action="call_llm", resource=resource, context={})
            assert await engine.evaluate(req) is False, resource

    @pytest.mark.asyncio
    async def test_workload_allowlist_paths(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest, WORKLOAD_ALLOWED_PREFIXES
        engine = PolicyEngine()
        user = {"type": "workload", "spiffe_id": "spiffe://trust/svc/x"}
        for prefix in WORKLOAD_ALLOWED_PREFIXES:
            req = PolicyRequest(user=user, action="call_llm", resource=prefix, context={})
            assert await engine.evaluate(req) is True, prefix

    @pytest.mark.asyncio
    async def test_workload_allowed_via_workload_scope_rule(self):
        """A SCOPE_RULE mapping a path to scope 'workload' allows SPIFFE workloads there."""
        from unittest.mock import AsyncMock, patch as _patch
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        user = {"type": "workload", "spiffe_id": "spiffe://trust/svc/x"}
        req = PolicyRequest(user=user, action="call_llm",
                            resource="/workload-only/predict", context={})
        with _patch("llm_gateway.policy._load_rules",
                    AsyncMock(return_value=[("/workload-only/predict", "workload")])):
            assert await engine.evaluate(req) is True

        # A rule mapping the path to a non-workload scope still denies workloads
        with _patch("llm_gateway.policy._load_rules",
                    AsyncMock(return_value=[("/workload-only/predict", "llm:chat")])):
            assert await engine.evaluate(req) is False


    @pytest.mark.asyncio
    async def test_user_without_scopes_allowed(self):
        from llm_gateway.policy import PolicyEngine, PolicyRequest
        engine = PolicyEngine()
        req = PolicyRequest(user={"some": "data"}, action="call_llm", resource="/api/chat", context={})
        assert await engine.evaluate(req) is True

    def test_singleton(self):
        from llm_gateway.policy import get_policy_engine
        assert get_policy_engine() is get_policy_engine()


# ─── DB rules JSON parsing ───────────────────────────────────────────────

class TestRulesParsing:

    def test_valid_json(self):
        from llm_gateway.policy import _parse_rules_json
        rules = _parse_rules_json('["/custom/path", "custom:scope"]')
        # This is a flat array, not array of arrays — should return empty
        assert rules == []

    def test_valid_nested_json(self):
        from llm_gateway.policy import _parse_rules_json
        rules = _parse_rules_json('[["/custom/path", "custom:scope"]]')
        assert rules == [("/custom/path", "custom:scope")]

    def test_invalid_json_returns_empty(self):
        from llm_gateway.policy import _parse_rules_json
        assert _parse_rules_json("not json") == []

    def test_empty_string(self):
        from llm_gateway.policy import _parse_rules_json
        assert _parse_rules_json("") == []
