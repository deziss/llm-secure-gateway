"""Unit tests for proxy_helpers.py"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# get_owner_id_from_request
# ---------------------------------------------------------------------------

class TestGetOwnerIdFromRequest:
    def _make_request(self, user=None):
        req = MagicMock()
        req.state = MagicMock()
        req.state.user = user
        return req

    def test_api_key_user(self):
        from llm_gateway.proxy_helpers import get_owner_id_from_request

        # APIKey has owner_id (plain FK string) — NOT the lazy 'owner' relationship
        user = MagicMock(spec=["owner_id"])
        user.owner_id = "user:alice"
        req = self._make_request(user)

        owner_id, is_admin = get_owner_id_from_request(req)
        assert owner_id == "user:alice"
        assert is_admin is False

    def test_admin_ui_user(self):
        from llm_gateway.proxy_helpers import get_owner_id_from_request
        import uuid

        uid = uuid.uuid4()
        user = MagicMock(spec=["role", "id"])
        user.role = "admin"
        user.id = uid
        req = self._make_request(user)

        owner_id, is_admin = get_owner_id_from_request(req)
        assert owner_id == str(uid)
        assert is_admin is True

    def test_manager_ui_user(self):
        from llm_gateway.proxy_helpers import get_owner_id_from_request
        import uuid

        user = MagicMock(spec=["role", "id"])
        user.role = "manager"
        user.id = uuid.uuid4()
        req = self._make_request(user)

        _, is_admin = get_owner_id_from_request(req)
        assert is_admin is True

    def test_developer_ui_user_not_admin(self):
        from llm_gateway.proxy_helpers import get_owner_id_from_request
        import uuid

        user = MagicMock(spec=["role", "id"])
        user.role = "developer"
        user.id = uuid.uuid4()
        req = self._make_request(user)

        _, is_admin = get_owner_id_from_request(req)
        assert is_admin is False

    def test_spiffe_user(self):
        from llm_gateway.proxy_helpers import get_owner_id_from_request

        user = {"spiffe_id": "spiffe://trust/svc/my-service"}
        req = self._make_request(user)

        owner_id, is_admin = get_owner_id_from_request(req)
        assert owner_id == "spiffe://trust/svc/my-service"
        assert is_admin is False

    def test_no_user_returns_anonymous(self):
        from llm_gateway.proxy_helpers import get_owner_id_from_request

        req = self._make_request(user=None)
        owner_id, is_admin = get_owner_id_from_request(req)
        assert owner_id == "anonymous"
        assert is_admin is False


# ---------------------------------------------------------------------------
# apply_rate_limit
# ---------------------------------------------------------------------------

class TestApplyRateLimit:
    def _make_request(self, key_hash="hash123", rate_limit_rpm=60):
        user = MagicMock()
        user.rate_limit_rpm = rate_limit_rpm
        user.key_hash = key_hash
        req = MagicMock()
        req.state = MagicMock()
        req.state.user = user
        return req

    def test_allows_when_under_limit(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            req = self._make_request(rate_limit_rpm=60)
            # Should not raise
            apply_rate_limit(req)

    def test_raises_429_when_exceeded(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for _ in range(60):
            limiter.check_rate_limit("exhaust_key", 60)

        user = MagicMock()
        user.rate_limit_rpm = 60
        user.key_hash = "exhaust_key"
        req = MagicMock()
        req.state.user = user

        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            with pytest.raises(HTTPException) as exc_info:
                apply_rate_limit(req)
            assert exc_info.value.status_code == 429

    def test_429_includes_retry_after_header(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for _ in range(10):
            limiter.check_rate_limit("retry_key", 10)

        user = MagicMock()
        user.rate_limit_rpm = 10
        user.key_hash = "retry_key"
        req = MagicMock()
        req.state.user = user

        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            with pytest.raises(HTTPException) as exc_info:
                apply_rate_limit(req)
            assert exc_info.value.status_code == 429
            assert "Retry-After" in exc_info.value.headers

    def test_no_user_does_not_raise(self):
        from llm_gateway.proxy_helpers import apply_rate_limit

        req = MagicMock()
        req.state = MagicMock(spec=[])  # no 'user' attr
        apply_rate_limit(req)

    def test_none_rpm_defaults_to_60(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        user = MagicMock()
        user.rate_limit_rpm = None
        user.key_hash = "none_rpm_key"
        req = MagicMock()
        req.state.user = user

        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            apply_rate_limit(req)


# ---------------------------------------------------------------------------
# check_owner_permissions
# ---------------------------------------------------------------------------

class TestCheckOwnerPermissions:
    def _make_backend(self, allowed_endpoints=None, name="test-backend"):
        backend = MagicMock()
        backend.name = name
        backend.allowed_endpoints = allowed_endpoints if allowed_endpoints is not None else ["v1/chat/completions"]
        return backend

    def _make_session(self, perms):
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = perms
        session.execute = AsyncMock(return_value=result)
        return session

    @pytest.mark.asyncio
    async def test_allows_with_wildcard_model(self):
        from llm_gateway.proxy_helpers import check_owner_permissions
        from llm_gateway.models import OwnerPermission

        perm = MagicMock(spec=OwnerPermission)
        perm.allowed_models = ["*"]

        session = self._make_session([perm])
        backend = self._make_backend()

        await check_owner_permissions(session, "user:alice", backend, "v1/chat/completions", "llama3")

    @pytest.mark.asyncio
    async def test_raises_403_no_permission_row(self):
        from llm_gateway.proxy_helpers import check_owner_permissions

        session = self._make_session([])
        backend = self._make_backend()

        with pytest.raises(HTTPException) as exc:
            await check_owner_permissions(session, "user:alice", backend, "v1/chat/completions", "llama3")
        assert exc.value.status_code == 403
        assert "no permissions" in exc.value.detail

    @pytest.mark.asyncio
    async def test_raises_403_model_not_allowed(self):
        from llm_gateway.proxy_helpers import check_owner_permissions
        from llm_gateway.models import OwnerPermission

        perm = MagicMock(spec=OwnerPermission)
        perm.allowed_models = ["mistral:latest"]

        session = self._make_session([perm])
        backend = self._make_backend()

        with pytest.raises(HTTPException) as exc:
            await check_owner_permissions(session, "user:alice", backend, "v1/chat/completions", "llama3")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_403_endpoint_blocked_by_backend(self):
        """Backend-level allowed_endpoints whitelist still enforced (different from owner perms)."""
        from llm_gateway.proxy_helpers import check_owner_permissions
        from llm_gateway.models import OwnerPermission

        perm = MagicMock(spec=OwnerPermission)
        perm.allowed_models = ["*"]

        session = self._make_session([perm])
        backend = self._make_backend(allowed_endpoints=["v1/chat/completions"])

        with pytest.raises(HTTPException) as exc:
            await check_owner_permissions(session, "user:alice", backend, "api/delete", None)
        assert exc.value.status_code == 403
        assert "not allowed" in exc.value.detail

    @pytest.mark.asyncio
    async def test_raises_403_empty_backend_allowed_endpoints(self):
        from llm_gateway.proxy_helpers import check_owner_permissions

        session = self._make_session([])
        backend = self._make_backend(allowed_endpoints=[])

        with pytest.raises(HTTPException) as exc:
            await check_owner_permissions(session, "user:alice", backend, "v1/chat/completions", None)
        assert exc.value.status_code == 403
        assert "No endpoints" in exc.value.detail

    @pytest.mark.asyncio
    async def test_allows_specific_model_permission(self):
        from llm_gateway.proxy_helpers import check_owner_permissions
        from llm_gateway.models import OwnerPermission

        perm = MagicMock(spec=OwnerPermission)
        perm.allowed_models = ["llama3:latest"]

        session = self._make_session([perm])
        backend = self._make_backend()

        # Should not raise
        await check_owner_permissions(session, "user:alice", backend, "v1/chat/completions", "llama3:latest")

    @pytest.mark.asyncio
    async def test_owner_allowed_endpoints_no_longer_enforced(self):
        """Owner-level allowed_endpoints is deprecated. Endpoint auth is via API key scopes."""
        from llm_gateway.proxy_helpers import check_owner_permissions
        from llm_gateway.models import OwnerPermission

        perm = MagicMock(spec=OwnerPermission)
        perm.allowed_models = ["*"]
        perm.allowed_endpoints = ["/api/embeddings"]  # restricted — would have blocked before

        session = self._make_session([perm])
        backend = self._make_backend(allowed_endpoints=["*"])

        # Should NOT raise — owner endpoint field is no longer enforced
        await check_owner_permissions(session, "user:alice", backend, "v1/chat/completions", "llama3")
