import os

# Application
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")

# SMTP Settings
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_TLS = os.getenv("SMTP_TLS", "true").lower() == "true"
EMAILS_FROM_EMAIL = os.getenv("EMAILS_FROM_EMAIL", "admin@llmgateway.io")
EMAILS_FROM_NAME = os.getenv("EMAILS_FROM_NAME", "LLM Gateway Admin")

# Admin Alert Email
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "superadmin@example.com")

# Dev mode: serve the admin UI over plain HTTP on a non-localhost address
# (e.g. http://<lan-ip>:6130). Browsers silently drop `Secure` cookies on such
# origins, so login never sticks. When true, cookies lose the Secure flag and
# HSTS is not sent -- session cookies and passwords then travel unencrypted.
# Never enable in production; put a TLS reverse proxy in front instead.
ALLOW_INSECURE_HTTP = os.getenv("ALLOW_INSECURE_HTTP", "false").lower() == "true"
