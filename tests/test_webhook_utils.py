"""Tests for webhook utility functions."""

import pytest


class TestWebhookPayloadExtraction:
    """Tests for webhook payload extraction utilities."""

    @pytest.mark.unit
    def test_get_nested_simple(self):
        """get_nested should extract simple paths."""
        from webhook.router import get_nested
        
        payload = {"level1": {"level2": "value"}}
        assert get_nested(payload, "level1.level2") == "value"

    @pytest.mark.unit
    def test_get_nested_missing(self):
        """get_nested should return None for missing paths."""
        from webhook.router import get_nested
        
        payload = {"level1": {"level2": "value"}}
        assert get_nested(payload, "level1.missing") is None
        assert get_nested(payload, "missing.path") is None

    @pytest.mark.unit
    def test_get_nested_root_level(self):
        """get_nested should handle root level keys."""
        from webhook.router import get_nested
        
        payload = {"key": "value"}
        assert get_nested(payload, "key") == "value"

    @pytest.mark.unit
    def test_first_value_finds_first_match(self):
        """first_value should return first non-empty value."""
        from webhook.router import first_value
        
        payload = {
            "a": {"x": None},
            "b": {"y": ""},
            "c": {"z": "found"},
        }
        result = first_value(payload, ["a.x", "b.y", "c.z"])
        assert result == "found"

    @pytest.mark.unit
    def test_first_value_returns_none_if_all_empty(self):
        """first_value should return None if all paths are empty."""
        from webhook.router import first_value
        
        payload = {"a": None}
        result = first_value(payload, ["a", "b", "c"])
        assert result is None

    @pytest.mark.unit
    def test_first_value_with_real_payload(self, sample_preauth_payload):
        """first_value should work with real payload structure."""
        from webhook.router import first_value
        
        # Should find patient name from enrollee
        result = first_value(sample_preauth_payload, [
            "patient.name",
            "enrollee.first_name",
        ])
        assert result == "John"

    @pytest.mark.unit
    def test_get_nested_with_real_payload(self, sample_preauth_payload):
        """get_nested should work with real payload structure."""
        from webhook.router import get_nested
        
        assert get_nested(sample_preauth_payload, "enrollee.insurance_no") == "INS123"
        assert get_nested(sample_preauth_payload, "policy.plan_name") == "Gold Plan"
        assert get_nested(sample_preauth_payload, "encounter.facility_name") == "General Hospital"


class TestMaskApiKey:
    """Tests for API key masking."""

    @pytest.mark.unit
    def test_mask_api_key_shows_last_chars(self):
        """mask_api_key should show only last few characters."""
        from webhook.router import mask_api_key
        
        key = "abc123def456ghi789"
        masked = mask_api_key(key)
        
        # Should not contain full key
        assert key not in masked
        # Should end with last few chars (implementation dependent)
        assert masked.endswith("789") or "***" in masked or "..." in masked

    @pytest.mark.unit
    def test_mask_api_key_handles_none(self):
        """mask_api_key should handle None gracefully."""
        from webhook.router import mask_api_key
        
        # Should not raise an exception
        result = mask_api_key(None)
        # None input returns None (no key to mask)
        assert result is None

    @pytest.mark.unit
    def test_mask_api_key_handles_empty(self):
        """mask_api_key should handle empty string."""
        from webhook.router import mask_api_key
        
        # Should not raise an exception
        result = mask_api_key("")
        # Empty string returns None (no key to mask)
        assert result is None
