"""Service layer — re-exports for backward-compatible imports."""
from .config_service import ConfigService, config_service, get_config_service
from .auth_service import AuthService, auth_service, get_auth_service
from .owner_service import OwnerService, owner_service, get_owner_service
from .bot_service import BotService, bot_service, get_bot_service
from .snippet_service import generate_client_snippets

__all__ = [
    "ConfigService", "config_service", "get_config_service",
    "AuthService", "auth_service", "get_auth_service",
    "OwnerService", "owner_service", "get_owner_service",
    "BotService", "bot_service", "get_bot_service",
    "generate_client_snippets",
]

