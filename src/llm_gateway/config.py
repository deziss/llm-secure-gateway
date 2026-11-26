import os
import logging

logger = logging.getLogger(__name__)

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


def _remember_days() -> int:
    try:
        return max(1, min(365, int(os.getenv("REMEMBER_ME_DAYS", "30"))))
    except ValueError:
        return 30


# "Remember me" on the login page keeps the session this long (default 30 days).
REMEMBER_ME_DAYS = _remember_days()

# SPIFFE workload identity (service-mesh sidecar) authentication.
# Disabled by default: a plain X-Spiffe-ID header is trivially spoofable by any
# client, so it must NOT authenticate anyone. Only enable when every request
# passes through a trusted sidecar that OVERWRITES the header and sets
# X-Spiffe-Token, and configure a shared SPIFFE_SIDECAR_TOKEN. If trusted-header
# is enabled but the token is unset, SPIFFE auth fails closed.
SPIFFE_TRUSTED_HEADER = os.getenv("SPIFFE_TRUSTED_HEADER", "false").lower() == "true"
SPIFFE_SIDECAR_TOKEN = os.getenv("SPIFFE_SIDECAR_TOKEN", "")

# Licencia Licensing Engine Configuration
LICENCIA_URL = os.getenv("LICENCIA_URL", "http://172.20.0.11:3000")
LICENCIA_API_KEY = os.getenv("LICENCIA_API_KEY", "")
LICENCIA_LICENSE_KEY = os.getenv("LICENCIA_LICENSE_KEY", "")
LICENCIA_LEASE_FILE = os.getenv("LICENCIA_LEASE_FILE", "/tmp/gateway_license_lease.json")


# ---------------------------------------------------------------------------
# Observability capture levels & retention tiers (governance)
# ---------------------------------------------------------------------------

def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an int env var, clamped to [minimum, maximum]; bad values warn and fall back."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default
    if value < minimum or value > maximum:
        logger.warning("%s=%s out of range [%s, %s], clamping", name, value, minimum, maximum)
        value = max(minimum, min(maximum, value))
    return value


def _capture_level() -> str:
    """OBSERVABILITY_CAPTURE_LEVEL: metadata | redacted | full (default metadata)."""
    level = os.getenv("OBSERVABILITY_CAPTURE_LEVEL", "metadata").strip().lower()
    if level not in ("metadata", "redacted", "full"):
        logger.warning(
            "Invalid OBSERVABILITY_CAPTURE_LEVEL=%r, using 'metadata'",
            os.getenv("OBSERVABILITY_CAPTURE_LEVEL"),
        )
        return "metadata"
    return level


# What prompt/response content is stored with observations (agent steps):
#   metadata  - no content at all (default, current behaviour)
#   redacted  - PII-redacted + truncated content (requires PII service; falls back to metadata)
#   full      - raw content, truncated to OBSERVABILITY_MAX_CONTENT_CHARS
OBSERVABILITY_CAPTURE_LEVEL = _capture_level()
# Max characters of prompt/response content stored per observation field.
OBSERVABILITY_MAX_CONTENT_CHARS = _bounded_int("OBSERVABILITY_MAX_CONTENT_CHARS", 2000, 100, 1_000_000)

# Retention windows (days). 0 = keep forever (never purged by the cleanup job).
RETENTION_DAYS_AUDIT = _bounded_int("RETENTION_DAYS_AUDIT", 0, 0, 3650)            # audit_log
RETENTION_DAYS_TRACES = _bounded_int("RETENTION_DAYS_TRACES", 90, 0, 3650)         # agent_runs/steps
RETENTION_DAYS_SPEND_METRICS = _bounded_int("RETENTION_DAYS_SPEND_METRICS", 0, 0, 3650)  # spend_records/rollups
# How often the background retention job runs (seconds, minimum 1h).
RETENTION_INTERVAL_SECONDS = _bounded_int("RETENTION_INTERVAL_SECONDS", 21600, 3600, 604800)

# Secret encryption for backend API keys at rest (services/secret_crypto.py).
# A Fernet key; empty = ephemeral fallback (keys won't survive restart).
GATEWAY_SECRET_KEY = os.getenv("GATEWAY_SECRET_KEY", "")
