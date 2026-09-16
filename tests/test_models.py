"""
Unit tests for the data models.
"""
import pytest
from datetime import datetime, timezone


class TestModels:
    """Test cases for SQLModel data models."""

    def test_llm_backend_creation(self, sample_backend_data):
        """Test creating an LLMBackend instance."""
        from llm_gateway.models import LLMBackend, BackendType
        
        backend = LLMBackend(**sample_backend_data)
        
        assert backend.name == sample_backend_data["name"]
        assert backend.base_url == sample_backend_data["base_url"]
        # Handle both enum and string values
        expected_type = sample_backend_data["backend_type"]
        actual_type = backend.backend_type.value if hasattr(backend.backend_type, 'value') else backend.backend_type
        assert actual_type == expected_type
        assert backend.models == sample_backend_data["models"]
        assert backend.allowed_endpoints == sample_backend_data["allowed_endpoints"]

    def test_llm_backend_default_values(self):
        """Test LLMBackend default values."""
        from llm_gateway.models import LLMBackend, BackendType
        
        backend = LLMBackend(name="test", base_url="http://localhost:11434")
        
        assert backend.backend_type == BackendType.OLLAMA
        assert backend.api_key is None
        assert backend.models == []
        assert backend.allowed_endpoints == []

    def test_api_key_create_schema(self, sample_api_key_data):
        """Test APIKeyCreate schema validation."""
        from llm_gateway.models import APIKeyCreate
        
        data = APIKeyCreate(**sample_api_key_data)
        
        assert data.owner == sample_api_key_data["owner"]
        assert data.scopes == sample_api_key_data["scopes"]
        assert data.rate_limit_rpm == sample_api_key_data["rate_limit_rpm"]
        assert data.expires_at is None

    def test_api_key_create_defaults(self):
        """Test APIKeyCreate default values."""
        from llm_gateway.models import APIKeyCreate
        
        data = APIKeyCreate(owner="user:123")
        
        assert data.scopes == ["chat"]
        assert data.rate_limit_rpm == 60
        assert data.expires_at is None

    def test_owner_creation(self, sample_owner_data):
        """Test creating an Owner instance."""
        from llm_gateway.models import Owner, OwnerType
        
        owner = Owner(**sample_owner_data)
        
        assert owner.id == sample_owner_data["id"]
        assert owner.name == sample_owner_data["name"]
        assert owner.email == sample_owner_data["email"]
        assert owner.type == OwnerType.USER
        assert owner.is_active == True
        assert owner.description == "Test owner for unit tests"
        assert owner.max_keys == 5

    def test_owner_defaults(self):
        """Test Owner default values for new fields."""
        from llm_gateway.models import Owner
        
        owner = Owner(id="min-owner", name="Minimal", type="user")
        
        assert owner.is_active == True
        assert owner.description is None
        assert owner.max_keys == 5
        assert owner.block_endpoints == True

    def test_owner_type_enum(self):
        """Test OwnerType enum values."""
        from llm_gateway.models import OwnerType
        
        assert OwnerType.USER.value == "user"
        assert OwnerType.PROJECT.value == "project"

    def test_backend_type_enum(self):
        """Test BackendType enum values."""
        from llm_gateway.models import BackendType
        
        assert BackendType.OLLAMA.value == "ollama"
        assert BackendType.VLLM.value == "vllm"
        assert BackendType.OPENAI.value == "openai"

    def test_api_key_scope_enum(self):
        """Test APIKeyScope enum values."""
        from llm_gateway.models import APIKeyScope
        
        assert APIKeyScope.CHAT.value == "chat"
        assert APIKeyScope.ADMIN.value == "admin"
        assert APIKeyScope.READ_ONLY.value == "read_only"


class TestAuthModels:
    """Test cases for authentication models."""

    def test_user_model_creation(self):
        """Test creating a User instance."""
        from llm_gateway.auth.models import User
        import uuid
        
        user = User(
            id=uuid.uuid4(),
            email="test@example.com",
            hashed_password="hashed_password_here"
        )
        
        assert user.email == "test@example.com"
        assert user.is_active is True
        assert user.is_superuser is False
        assert user.is_verified is False

    def test_user_model_with_timestamps(self):
        """Test User model with timestamp fields."""
        from llm_gateway.auth.models import User
        import uuid
        
        user = User(
            id=uuid.uuid4(),
            email="test@example.com",
            hashed_password="hashed",
            created_at=datetime.now(timezone.utc)
        )
        
        assert user.created_at is not None
        assert user.updated_at is None
        assert user.last_login is None
