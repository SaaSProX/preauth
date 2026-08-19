"""Tests for auth/utils.py"""

import pytest
import jwt
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from auth.utils import (
    hash_password,
    verify_password,
    generate_api_key,
    generate_session_token,
    verify_session_token,
)


class TestPasswordHashing:
    """Tests for password hashing functions."""

    @pytest.mark.unit
    def test_hash_password_returns_string(self):
        """hash_password should return a string."""
        result = hash_password("test_password")
        assert isinstance(result, str)

    @pytest.mark.unit
    def test_hash_password_different_each_time(self):
        """hash_password should return different hashes (salted)."""
        hash1 = hash_password("same_password")
        hash2 = hash_password("same_password")
        assert hash1 != hash2

    @pytest.mark.unit
    def test_verify_password_correct(self):
        """verify_password should return True for correct password."""
        password = "my_secure_password"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    @pytest.mark.unit
    def test_verify_password_incorrect(self):
        """verify_password should return False for wrong password."""
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False

    @pytest.mark.unit
    def test_verify_password_empty(self):
        """verify_password should handle empty password."""
        hashed = hash_password("")
        assert verify_password("", hashed) is True
        assert verify_password("not_empty", hashed) is False


class TestApiKeyGeneration:
    """Tests for API key generation."""

    @pytest.mark.unit
    def test_generate_api_key_returns_string(self):
        """generate_api_key should return a string."""
        result = generate_api_key()
        assert isinstance(result, str)

    @pytest.mark.unit
    def test_generate_api_key_length(self):
        """generate_api_key should return 64 character hex string."""
        result = generate_api_key()
        assert len(result) == 64  # 32 bytes = 64 hex chars

    @pytest.mark.unit
    def test_generate_api_key_unique(self):
        """generate_api_key should return unique keys."""
        keys = [generate_api_key() for _ in range(100)]
        assert len(set(keys)) == 100  # All unique

    @pytest.mark.unit
    def test_generate_api_key_hex_format(self):
        """generate_api_key should return valid hex string."""
        result = generate_api_key()
        # Should not raise ValueError
        int(result, 16)


class TestSessionToken:
    """Tests for session token generation and verification."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings with test JWT secret."""
        with patch("auth.utils.settings") as mock:
            mock.jwt_secret = "test_secret_key_for_testing"
            yield mock

    @pytest.mark.unit
    def test_generate_session_token_returns_string(self, mock_settings):
        """generate_session_token should return a string."""
        token = generate_session_token(
            client_id=1,
            email="test@example.com",
            org_id=1,
            role="admin"
        )
        assert isinstance(token, str)

    @pytest.mark.unit
    def test_generate_session_token_is_valid_jwt(self, mock_settings):
        """generate_session_token should return valid JWT."""
        token = generate_session_token(
            client_id=1,
            email="test@example.com",
            org_id=1,
            role="admin"
        )
        # Should be decodable
        payload = jwt.decode(token, "test_secret_key_for_testing", algorithms=["HS256"])
        assert payload["sub"] == "1"
        assert payload["email"] == "test@example.com"
        assert payload["org_id"] == 1
        assert payload["role"] == "admin"

    @pytest.mark.unit
    def test_session_token_has_expiry(self, mock_settings):
        """Session token should have expiration."""
        from datetime import timezone
        
        token = generate_session_token(
            client_id=1,
            email="test@example.com",
            org_id=1,
            role="admin"
        )
        payload = jwt.decode(token, "test_secret_key_for_testing", algorithms=["HS256"])
        assert "exp" in payload
        # Expiry should be in the future
        exp_time = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        assert exp_time > datetime.now(timezone.utc)

    @pytest.mark.unit
    def test_verify_session_token_missing_header(self, mock_settings):
        """verify_session_token should reject missing auth header."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        
        with pytest.raises(Exception) as exc_info:
            verify_session_token(mock_request)
        assert "Missing token" in str(exc_info.value.detail)

    @pytest.mark.unit
    def test_verify_session_token_invalid_format(self, mock_settings):
        """verify_session_token should reject non-Bearer format."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "Basic abc123"
        
        with pytest.raises(Exception) as exc_info:
            verify_session_token(mock_request)
        assert "Missing token" in str(exc_info.value.detail)

    @pytest.mark.unit
    def test_verify_session_token_invalid_jwt(self, mock_settings):
        """verify_session_token should reject invalid JWT."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "Bearer invalid_token_here"
        
        with pytest.raises(Exception) as exc_info:
            verify_session_token(mock_request)
        assert "Invalid token" in str(exc_info.value.detail)

    @pytest.mark.unit
    def test_verify_session_token_valid(self, mock_settings):
        """verify_session_token should accept valid token."""
        # Generate a valid token
        token = generate_session_token(
            client_id=42,
            email="user@example.com",
            org_id=5,
            role="member"
        )
        
        mock_request = MagicMock()
        mock_request.headers.get.return_value = f"Bearer {token}"
        
        payload = verify_session_token(mock_request)
        assert payload["sub"] == "42"
        assert payload["email"] == "user@example.com"
        assert payload["org_id"] == 5
        assert payload["role"] == "member"
