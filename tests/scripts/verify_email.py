import asyncio
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

from llm_gateway.services.email_service import get_email_service
from llm_gateway.config import ADMIN_EMAIL

async def main():
    print("Initializing EmailService...")
    service = get_email_service()
    
    target_email = "kushawahaanshu8858@gmail.com"
    print(f"Attempting to send test email to {target_email}...")
    try:
        await service.send_admin_alert(
            "Test Email 🧪", 
            f"This is a manual test sent to {target_email}.\nIf you are reading this, the integration is working!"
        )
        # Hack to force send to specific email if the service method hardcodes admin_email
        # Actually email_service.send_admin_alert sends to self.admin_email. 
        # I should use the internal _send method or welcome email for this test to target the specific user.
        
        await service._send(
            target_email,
            "Direct Test Message 📧",
            "<h1>It Works!</h1><p>This is a test email from your LLM Gateway.</p>"
        )

        print("✅ Email send command issued (check server logs for success/failure details if simple logging is used).")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

if __name__ == "__main__":
    asyncio.run(main())
