"""
Unit tests for the email service module.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestEmailService:
    def _make_service(self):
        from llm_gateway.services.email_service import EmailService
        service = EmailService()
        return service

    @patch("llm_gateway.services.email_service.SMTP_HOST", "")
    @patch("llm_gateway.services.email_service.SMTP_USER", "")
    async def test_send_skips_when_smtp_not_configured(self):
        service = self._make_service()
        # Should not raise; just logs a warning
        await service._send("user@test.com", "Subject", "<p>Body</p>")

    @patch("llm_gateway.services.email_service.SMTP_HOST", "smtp.test.com")
    @patch("llm_gateway.services.email_service.SMTP_USER", "user")
    @patch("llm_gateway.services.email_service.aiosmtplib")
    async def test_send_calls_aiosmtplib(self, mock_smtp):
        mock_smtp.send = AsyncMock()
        service = self._make_service()
        await service._send("user@test.com", "Subject", "<p>Body</p>")
        mock_smtp.send.assert_awaited_once()

    @patch("llm_gateway.services.email_service.SMTP_HOST", "smtp.test.com")
    @patch("llm_gateway.services.email_service.SMTP_USER", "user")
    @patch("llm_gateway.services.email_service.aiosmtplib")
    async def test_send_handles_smtp_error_gracefully(self, mock_smtp):
        mock_smtp.send = AsyncMock(side_effect=Exception("SMTP connection refused"))
        service = self._make_service()
        # Should not raise
        await service._send("user@test.com", "Subject", "<p>Body</p>")

    def test_render_template(self):
        service = self._make_service()
        # Mock the Jinja2 environment
        mock_template = MagicMock()
        mock_template.render.return_value = "<p>Hello test</p>"
        service.env = MagicMock()
        service.env.get_template.return_value = mock_template

        result = service._render_template("welcome.html", {"email": "test@test.com"})
        assert result == "<p>Hello test</p>"
        service.env.get_template.assert_called_once_with("welcome.html")

    async def test_send_welcome_email(self):
        service = self._make_service()
        service._render_template = MagicMock(return_value="<p>Welcome</p>")
        service._send = AsyncMock()

        await service.send_welcome_email("new@test.com")
        service._send.assert_awaited_once()
        call_args = service._send.call_args
        assert call_args[0][0] == "new@test.com"
        assert "Welcome" in call_args[0][1]

    async def test_send_reset_password_email(self):
        service = self._make_service()
        service._render_template = MagicMock(return_value="<p>Reset</p>")
        service._send = AsyncMock()

        await service.send_reset_password_email("user@test.com", "token123", "http://localhost")
        service._send.assert_awaited_once()
        call_args = service._send.call_args
        assert call_args[0][0] == "user@test.com"

    async def test_send_admin_alert(self):
        service = self._make_service()
        service._render_template = MagicMock(return_value="<p>Alert</p>")
        service._send = AsyncMock()

        await service.send_admin_alert("Test Alert", "Something happened")
        service._send.assert_awaited_once()
        call_args = service._send.call_args
        assert call_args[0][0] == service.admin_email

    def test_get_email_service_returns_singleton(self):
        from llm_gateway.services.email_service import get_email_service
        s1 = get_email_service()
        s2 = get_email_service()
        assert s1 is s2
