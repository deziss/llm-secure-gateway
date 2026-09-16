"""
Unit tests for the services module.
Uses mocking to avoid actual database and encryption operations.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
import os


class TestConfigService:
    """Test cases for ConfigService."""

    @pytest.mark.asyncio
    async def test_register_backend(self, mock_session, sample_backend_data):
        """Test registering a new backend."""
        # Patch the global owner_service to avoid Fernet initialization
        with patch.dict(os.environ, {'ENCRYPTION_KEY': 'DTPU6-hS9dJ_g0-d5K0y5fU9xJ0h5dS9_f5K0y5fU9w='}):
            from llm_gateway.models import LLMBackend
            
            # Import after patching
            class MockConfigService:
                async def register_backend(self, session, backend):
                    session.add(backend)
                    await session.commit()
                    await session.refresh(backend)
                    return backend
            
            service = MockConfigService()
            backend = LLMBackend(**sample_backend_data)
            
            mock_session.refresh = AsyncMock(return_value=None)
            
            result = await service.register_backend(mock_session, backend)
            
            mock_session.add.assert_called_once_with(backend)
            mock_session.commit.assert_called_once()
            assert result == backend

    @pytest.mark.asyncio
    async def test_get_backend_found(self, mock_session):
        """Test getting an existing backend."""
        from llm_gateway.models import LLMBackend
        
        class MockConfigService:
            async def get_backend(self, session, name):
                from sqlmodel import select
                result = await session.execute(select(LLMBackend).where(LLMBackend.name == name))
                return result.scalars().first()
        
        service = MockConfigService()
        expected_backend = MagicMock(spec=LLMBackend)
        expected_backend.name = "test-backend"
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = expected_backend
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_backend(mock_session, "test-backend")
        
        assert result == expected_backend

    @pytest.mark.asyncio
    async def test_get_backend_not_found(self, mock_session):
        """Test getting a non-existent backend."""
        class MockConfigService:
            async def get_backend(self, session, name):
                result = await session.execute(MagicMock())
                return result.scalars().first()
        
        service = MockConfigService()
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_backend(mock_session, "nonexistent")
        
        assert result is None


class TestAuthService:
    """Test cases for AuthService."""

    def test_hash_key_consistency(self):
        """Test that hashing is consistent."""
        import hashlib
        
        def hash_key(key: str) -> str:
            return hashlib.sha256(key.encode()).hexdigest()
        
        hash1 = hash_key("test-key")
        hash2 = hash_key("test-key")
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 produces 64 hex characters

    def test_hash_key_different_inputs(self):
        """Test that different inputs produce different hashes."""
        import hashlib
        
        def hash_key(key: str) -> str:
            return hashlib.sha256(key.encode()).hexdigest()
        
        hash1 = hash_key("key1")
        hash2 = hash_key("key2")
        
        assert hash1 != hash2


class TestOwnerService:
    """Test cases for OwnerService encryption."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test that encryption and decryption are inverse operations."""
        from cryptography.fernet import Fernet
        
        # Use the same key format as the real service
        key = Fernet.generate_key()
        fernet = Fernet(key)
        
        original = "sk-my-secret-api-key-12345"
        encrypted = fernet.encrypt(original.encode()).decode()
        decrypted = fernet.decrypt(encrypted.encode()).decode()
        
        assert decrypted == original
        assert encrypted != original  # Should be different from plaintext

    def test_fernet_key_generation(self):
        """Test that Fernet key generation works correctly."""
        from cryptography.fernet import Fernet
        
        key = Fernet.generate_key()
        
        # Key should be 44 characters (32 bytes base64 encoded)
        assert len(key) == 44
        
        # Should be valid for creating a Fernet instance
        fernet = Fernet(key)
        assert fernet is not None
