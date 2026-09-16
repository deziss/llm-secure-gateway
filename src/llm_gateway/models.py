from enum import Enum
from typing import Optional, List, Dict
from sqlmodel import SQLModel, Field, Column, Relationship
from sqlalchemy import JSON, UniqueConstraint
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class BackendType(str, Enum):
    OLLAMA = "ollama"
    VLLM = "vllm"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    GROQ = "groq"
    CUSTOM = "custom"

class LLMBackend(SQLModel, table=True):
    name: str = Field(primary_key=True, index=True, description="Unique name for the backend")
    base_url: str = Field(description="Base URL of the LLM server")
    backend_type: BackendType = Field(default=BackendType.OLLAMA)
    api_key: Optional[str] = Field(default=None, description="API Key for the backend (encrypted at rest)")
    models: List[str] = Field(default_factory=list, sa_column=Column(JSON), description="List of available models")
    fallback_urls: List[str] = Field(default_factory=list, sa_column=Column(JSON), description="Fallback backend URLs tried when base_url fails")
    allowed_endpoints: List[str] = Field(default_factory=list, sa_column=Column(JSON), description="List of allowed API endpoints")
    translation_mode: str = Field(default="none", description="Protocol Translation Mode (none, anthropic_to_openai, openai_to_anthropic)")
    normalize_thinking: bool = Field(default=False, description="Whether to extract and normalize thinking blocks (<think>...</think>)")
    weight: int = Field(default=100, description="Load balancing weight (higher = more traffic)")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: Optional[datetime] = Field(default=None)

class APIKeyScope(str, Enum):
    """Hierarchical scopes for API key access control.

    Format: namespace:action.  Use namespace:* for wildcard.
    Legacy flat scopes (chat, embeddings, etc.) are auto-aliased.
    """
    # Hierarchical (preferred for new keys)
    LLM_CHAT = "llm:chat"
    LLM_EMBED = "llm:embed"
    LLM_READ = "llm:read"
    LLM_ALL = "llm:*"
    ADMIN_ALL = "admin:*"
    WILDCARD = "*"

    # Legacy flat scopes (backward-compatible, aliased at evaluation time)
    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    ADMIN = "admin"
    READ_ONLY = "read_only"

# Separate schema from DB model (no inheritance)
class APIKeyCreate(SQLModel):
    owner: str = Field(description="Owner of the key (user/team)")
    scopes: List[str] = Field(default=["chat"])
    expires_at: Optional[datetime] = None
    rate_limit_rpm: Optional[int] = Field(default=60, description="Requests per minute limit")

class APIKey(SQLModel, table=True):
    key_hash: str = Field(primary_key=True, index=True)
    prefix: str = Field(index=True)  # Added index for revoke_key lookups
    owner_id: str = Field(foreign_key="owner.id", index=True, description="Owner of the key")
    scopes: List[str] = Field(sa_column=Column(JSON))
    expires_at: Optional[datetime] = None
    rate_limit_rpm: Optional[int] = Field(default=60)
    monthly_budget_usd: Optional[float] = Field(default=None, description="Monthly spend limit in USD (None=unlimited)")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    is_active: bool = True

    # Back-reference to Owner
    owner: Optional["Owner"] = Relationship(back_populates="api_keys")

class Token(SQLModel):
    access_token: str
    token_type: str

class OwnerType(str, Enum):
    USER = "user"
    PROJECT = "project"

class Owner(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True, description="Unique identifier (e.g., 'user:123')")
    type: OwnerType = Field(description="Type of owner")
    name: str = Field(description="Human-readable name")
    email: Optional[str] = Field(default=None, description="Contact email")
    user_id: Optional[str] = Field(default=None, description="Linked Auth User ID", index=True)
    block_endpoints: bool = Field(default=True, description="Whether to block dangerous endpoints (/api/pull, etc.) for this owner")
    is_active: bool = Field(default=True, description="Whether this owner is currently active")
    description: Optional[str] = Field(default=None, description="Description of this owner/project")
    max_keys: int = Field(default=5, description="Maximum number of active API keys allowed")
    monthly_budget_usd: Optional[float] = Field(default=None, description="Monthly spend limit in USD (None=unlimited)")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    
    # Relationships for cascade operations
    api_keys: List["APIKey"] = Relationship(back_populates="owner", sa_relationship_kwargs={"cascade": "all, delete-orphan", "foreign_keys": "[APIKey.owner_id]"})
    provider_keys: List["ProviderKey"] = Relationship(back_populates="owner", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    permissions: List["OwnerPermission"] = Relationship(back_populates="owner", sa_relationship_kwargs={"cascade": "all, delete-orphan"})


# Response model for Owner (excludes relationships to prevent serialization issues)
class OwnerResponse(SQLModel):
    id: str
    type: OwnerType
    name: str
    email: Optional[str] = None
    block_endpoints: bool = True
    is_active: bool = True
    description: Optional[str] = None
    max_keys: int = 5
    created_at: datetime
    updated_at: Optional[datetime] = None

class ProviderKey(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("owner_id", "provider_id", name="uq_owner_provider"),)
    
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: str = Field(foreign_key="owner.id", index=True, description="Owner of this key")
    provider_id: str = Field(index=True, description="Provider identifier (e.g. 'openai')")
    encrypted_key: str = Field(description="Encrypted API key")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    
    # Back-reference
    owner: Optional[Owner] = Relationship(back_populates="provider_keys")

class OwnerPermission(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("owner_id", "backend_name", name="uq_owner_backend_perm"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: str = Field(foreign_key="owner.id", index=True, description="Owner of this permission")
    backend_name: str = Field(foreign_key="llmbackend.name", index=True, description="Allowed backend")
    allowed_models: List[str] = Field(default_factory=lambda: ["*"], sa_column=Column(JSON), description="List of allowed models, or ['*'] for all")
    allowed_endpoints: List[str] = Field(default_factory=lambda: ["*"], sa_column=Column(JSON), description="DEPRECATED: No longer enforced. Endpoint auth is handled by API key scopes.")
    created_at: datetime = Field(default_factory=_utcnow)

    # Back-reference
    owner: Optional[Owner] = Relationship(back_populates="permissions")

class SystemSetting(SQLModel, table=True):
    key: str = Field(primary_key=True, description="Configuration key (e.g., REQUIRE_INVITE)")
    value: str = Field(description="Configuration value")
    updated_at: datetime = Field(default_factory=_utcnow)

class AuditLog(SQLModel, table=True):
    """Persistent audit trail. Written when ENABLE_AUDIT_DB is true."""
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=_utcnow, index=True)
    event_type: str = Field(index=True, description="e.g. policy_eval, key_created, key_revoked")
    identity: str = Field(index=True, description="e.g. apikey:dev-team, spiffe:..., unknown")
    resource: str = Field(description="Affected resource path or ID")
    decision: str = Field(description="allow, deny, error")
    ip_address: Optional[str] = Field(default=None, description="Client IP address")
    metadata_json: dict = Field(default_factory=dict, sa_column=Column(JSON), description="Additional context")


class InviteCode(SQLModel, table=True):
    code: str = Field(primary_key=True, description="Unique invite code")
    created_by: str = Field(description="Admin user email who created this invite")
    used_by: Optional[str] = Field(default=None, description="Email of user who claimed this invite")
    is_used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)


class LLMBot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, description="Name of the bot")
    platform: str = Field(default="telegram", description="Bot platform (telegram or discord)")
    encrypted_token: str = Field(description="Encrypted API token")
    backend_name: str = Field(foreign_key="llmbackend.name", description="Target backend")
    model_name: Optional[str] = Field(default=None, description="Optional model override")
    system_prompt: Optional[str] = Field(default=None, description="Optional system prompt override")
    webhook_secret: Optional[str] = Field(default=None, description="Secret token for webhooks")
    enabled: bool = Field(default=True, description="Whether the bot is active")
    history_limit: int = Field(default=10, description="Max conversation messages to retain in memory context")
    created_at: datetime = Field(default_factory=_utcnow)


class LLMBotMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="llmbot.id", ondelete="CASCADE", description="Associated bot")
    chat_id: str = Field(index=True, description="Unique chat or channel ID from platform")
    role: str = Field(description="Message role: user or assistant")
    content: str = Field(description="Message content")
    created_at: datetime = Field(default_factory=_utcnow)


class LLMBotResponse(SQLModel):
    id: int
    name: str
    platform: str
    backend_name: str
    model_name: Optional[str] = None
    system_prompt: Optional[str] = None
    enabled: bool
    history_limit: int
    created_at: datetime

# ── v0.6.0: Resilience & Financial Governance ────────────────────────


class FallbackChain(SQLModel, table=True):
    """Ordered list of provider+model targets for automatic rerouting on 429/5xx."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, description="Human-readable chain name, e.g. 'production-code'")
    targets: List[Dict] = Field(default_factory=list, sa_column=Column(JSON),
        description='Ordered list of targets: [{"backend_name": "...", "model": "...", "translate": false}]')
    created_at: datetime = Field(default_factory=_utcnow)


class ModelAlias(SQLModel, table=True):
    """Virtual model name that resolves to a real provider+model combo."""
    alias: str = Field(primary_key=True, description="Virtual model name, e.g. 'smart-model'")
    backend_name: str = Field(foreign_key="llmbackend.name", description="Target backend")
    model_name: str = Field(description="Real model name on that backend")
    fallback_chain_id: Optional[int] = Field(default=None, foreign_key="fallbackchain.id",
        description="Optional fallback chain to use on failure")
    created_at: datetime = Field(default_factory=_utcnow)


class SpendRecord(SQLModel, table=True):
    """Per-request cost tracking for budget enforcement."""
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: str = Field(index=True, description="Owner who incurred this cost")
    api_key_hash: Optional[str] = Field(default=None, index=True, description="API key used")
    model: str = Field(description="Model name used")
    provider: str = Field(description="Provider type")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cost_usd: float = Field(default=0.0, description="Calculated cost in USD")
    created_at: datetime = Field(default_factory=_utcnow, index=True)


# ── v0.7.0: Performance & Caching ────────────────────────────────────


class CachedResponse(SQLModel, table=True):
    """Exact-match response cache for deterministic LLM requests."""
    cache_key: str = Field(primary_key=True, description="SHA-256 hash of (model, messages, temperature)")
    model: str = Field(description="Model name")
    response_json: str = Field(description="Full JSON response blob")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    hit_count: int = Field(default=0, description="Number of cache hits")
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime = Field(description="Expiry timestamp for TTL enforcement")

