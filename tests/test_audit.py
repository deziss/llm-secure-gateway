"""Unit tests for audit.py — stdout logging + DB persistence."""
import json
import logging
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class CaptureHandler(logging.Handler):
    """A logging handler that captures log messages for test assertions."""

    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


class TestAuditLogger:

    def test_log_api_key_user(self):
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        cap = CaptureHandler()
        logger._log.addHandler(cap)

        user = MagicMock()
        user.owner_id = "user:alice"

        logger.log("proxy_request", user, "/v1/chat/completions", "allow")
        logger._log.removeHandler(cap)

        assert len(cap.records) == 1
        entry = json.loads(cap.records[0])
        assert entry["identity"] == "apikey:user:alice"
        assert entry["event"] == "proxy_request"
        assert entry["decision"] == "allow"
        assert entry["resource"] == "/v1/chat/completions"

    def test_log_spiffe_workload(self):
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        cap = CaptureHandler()
        logger._log.addHandler(cap)

        user = {"type": "workload", "spiffe_id": "spiffe://trust/ns/sa/svc"}
        logger.log("proxy_request", user, "/api/chat", "allow", metadata={"model": "llama3"})
        logger._log.removeHandler(cap)

        entry = json.loads(cap.records[0])
        assert entry["identity"] == "spiffe:spiffe://trust/ns/sa/svc"
        assert entry["metadata"] == {"model": "llama3"}

    def test_log_unknown_user(self):
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        cap = CaptureHandler()
        logger._log.addHandler(cap)
        logger.log("policy_deny", "not-a-user", "/v1/completions", "deny")
        logger._log.removeHandler(cap)

        entry = json.loads(cap.records[0])
        assert entry["identity"] == "unknown"

    def test_log_no_metadata_defaults_to_empty(self):
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        cap = CaptureHandler()
        logger._log.addHandler(cap)
        logger.log("test_event", "user", "/path", "allow")
        logger._log.removeHandler(cap)

        entry = json.loads(cap.records[0])
        assert entry["metadata"] == {}

    def test_get_audit_logger_singleton(self):
        from llm_gateway.audit import get_audit_logger
        assert get_audit_logger() is get_audit_logger()

    def test_ip_address_passed_to_persist(self):
        """ip_address kwarg is accepted without error."""
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        cap = CaptureHandler()
        logger._log.addHandler(cap)
        user = MagicMock()
        user.owner_id = "user:test"
        # Should not raise — ip_address is accepted
        logger.log("test", user, "/path", "allow", ip_address="10.0.0.1")
        logger._log.removeHandler(cap)
        assert len(cap.records) == 1


class TestAuditDbPersistence:

    @pytest.mark.asyncio
    async def test_persist_skipped_when_disabled(self):
        """When ENABLE_AUDIT_DB=false, no DB commit occurs."""
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        entry = {
            "timestamp": "2026-04-01T00:00:00+00:00",
            "event": "test",
            "identity": "test",
            "resource": "/test",
            "decision": "allow",
            "metadata": {},
        }

        mock_session = AsyncMock()
        mock_config = MagicMock()
        mock_config.get_setting = AsyncMock(return_value="false")

        with patch("llm_gateway.database._async_session") as mock_maker:
            mock_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_maker.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("llm_gateway.services.get_config_service", return_value=mock_config):
                await logger._persist_to_db(entry, "127.0.0.1")

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_writes_when_enabled(self):
        """When ENABLE_AUDIT_DB=true, record is added and committed."""
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        entry = {
            "timestamp": "2026-04-01T00:00:00+00:00",
            "event": "policy_eval",
            "identity": "apikey:dev-team",
            "resource": "/api/chat",
            "decision": "allow",
            "metadata": {"method": "POST"},
        }

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_config = MagicMock()
        mock_config.get_setting = AsyncMock(return_value="true")

        with patch("llm_gateway.database._async_session") as mock_maker:
            mock_maker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_maker.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("llm_gateway.services.get_config_service", return_value=mock_config):
                await logger._persist_to_db(entry, "10.0.0.1")

        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_raise(self):
        """DB errors are caught — _persist_to_db never raises."""
        from llm_gateway.audit import AuditLogger

        logger = AuditLogger()
        entry = {"timestamp": "2026-04-01T00:00:00+00:00", "event": "test",
                 "identity": "x", "resource": "/x", "decision": "allow", "metadata": {}}

        with patch("llm_gateway.database._async_session", side_effect=Exception("DB down")):
            # Should NOT raise
            await logger._persist_to_db(entry, None)
