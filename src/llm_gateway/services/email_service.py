import logging
import asyncio
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from ..config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_TLS,
    EMAILS_FROM_EMAIL, EMAILS_FROM_NAME, ADMIN_EMAIL, APP_BASE_URL,
)

logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        self.template_dir = Path(__file__).parent.parent / "templates" / "email"
        self.env = Environment(loader=FileSystemLoader(str(self.template_dir)))
        self.admin_email = ADMIN_EMAIL

    async def _send(self, to_email: str, subject: str, html_content: str) -> None:
        if not SMTP_HOST or not SMTP_USER:
            logger.warning(f"SMTP not configured. Skipping email to {to_email}. Subject: {subject}")
            logger.info("Content preview: " + html_content[:200] + "...")
            return

        message = MIMEMultipart()
        message["From"] = f"{EMAILS_FROM_NAME} <{EMAILS_FROM_EMAIL}>"
        message["To"] = to_email
        message["Subject"] = subject
        message.attach(MIMEText(html_content, "html"))

        try:
            await aiosmtplib.send(
                message,
                hostname=SMTP_HOST,
                port=SMTP_PORT,
                username=SMTP_USER,
                password=SMTP_PASSWORD,
                start_tls=SMTP_TLS,
                timeout=5.0,
            )
            logger.info(f"Email sent to {to_email}")
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")

    def _render_template(self, template_name: str, context: dict) -> str:
        template = self.env.get_template(template_name)
        return template.render(**context)

    async def send_welcome_email(self, user_email: str) -> None:
        subject = "Welcome to LLM Gateway"
        context = {"email": user_email, "login_url": f"{APP_BASE_URL}/auth/login"}
        html = self._render_template("welcome.html", context)
        await self._send(user_email, subject, html)

    def send_welcome_email_background(self, user_email: str) -> None:
        asyncio.create_task(self.send_welcome_email(user_email))

    async def send_reset_password_email(self, user_email: str, token: str, base_url: str) -> None:
        subject = "Reset Your Password 🔑"
        reset_link = f"{base_url}/auth/reset_password?token={token}"
        context = {"email": user_email, "reset_link": reset_link}
        html = self._render_template("reset_password.html", context)
        await self._send(user_email, subject, html)

    def send_reset_password_email_background(self, user_email: str, token: str, base_url: str) -> None:
        asyncio.create_task(self.send_reset_password_email(user_email, token, base_url))

    async def send_admin_alert(self, title: str, details: str) -> None:
        subject = f"🚨 Admin Alert: {title}"
        context = {"title": title, "details": details}
        html = self._render_template("admin_alert.html", context)
        await self._send(self.admin_email, subject, html)

    def send_admin_alert_background(self, title: str, details: str) -> None:
        asyncio.create_task(self.send_admin_alert(title, details))

email_service = EmailService()

def get_email_service() -> EmailService:
    return email_service
