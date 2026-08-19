"""Tests for services/json_utils.py"""

import json
import pytest
from services.json_utils import normalize_json_value, parse_json_field, json_param


class TestNormalizeJsonValue:
    """Tests for normalize_json_value function."""

    @pytest.mark.unit
    def test_returns_dict_unchanged(self):
        """Dict values should pass through unchanged."""
        data = {"key": "value", "nested": {"inner": 123}}
        result = normalize_json_value(data)
        assert result == data

    @pytest.mark.unit
    def test_returns_list_unchanged(self):
        """List values should pass through unchanged."""
        data = [1, 2, 3, {"key": "value"}]
        result = normalize_json_value(data)
        assert result == data

    @pytest.mark.unit
    def test_returns_int_unchanged(self):
        """Integer values should pass through unchanged."""
        assert normalize_json_value(42) == 42

    @pytest.mark.unit
    def test_returns_none_unchanged(self):
        """None values should pass through unchanged."""
        assert normalize_json_value(None) is None

    @pytest.mark.unit
    def test_parses_json_string(self):
        """JSON string should be parsed to dict."""
        json_str = '{"key": "value"}'
        result = normalize_json_value(json_str)
        assert result == {"key": "value"}

    @pytest.mark.unit
    def test_parses_nested_json_string(self):
        """Double-encoded JSON should be unwrapped."""
        inner = {"key": "value"}
        double_encoded = json.dumps(json.dumps(inner))
        result = normalize_json_value(double_encoded)
        assert result == inner

    @pytest.mark.unit
    def test_handles_empty_string(self):
        """Empty string should return unchanged."""
        assert normalize_json_value("") == ""

    @pytest.mark.unit
    def test_handles_whitespace_string(self):
        """Whitespace-only string should return unchanged."""
        assert normalize_json_value("   ") == "   "

    @pytest.mark.unit
    def test_handles_invalid_json(self):
        """Invalid JSON string should return unchanged."""
        invalid = "not valid json {"
        assert normalize_json_value(invalid) == invalid

    @pytest.mark.unit
    def test_handles_plain_string(self):
        """Plain string (not JSON) should return unchanged."""
        plain = "hello world"
        assert normalize_json_value(plain) == plain

    @pytest.mark.unit
    def test_respects_max_depth(self):
        """Should not unwrap beyond max_depth."""
        # Triple-encoded with max_depth=2 should leave one layer
        inner = {"key": "value"}
        triple_encoded = json.dumps(json.dumps(json.dumps(inner)))
        result = normalize_json_value(triple_encoded, max_depth=2)
        # After 2 iterations, we should have the inner dict as a string
        assert result == json.dumps(inner)

    @pytest.mark.unit
    def test_parses_json_array_string(self):
        """JSON array string should be parsed."""
        json_str = '[1, 2, 3]'
        result = normalize_json_value(json_str)
        assert result == [1, 2, 3]


class TestParseJsonField:
    """Tests for parse_json_field function."""

    @pytest.mark.unit
    def test_is_alias_for_normalize(self):
        """parse_json_field should work like normalize_json_value."""
        data = '{"test": true}'
        assert parse_json_field(data) == normalize_json_value(data)

    @pytest.mark.unit
    def test_handles_none(self):
        """Should handle None input."""
        assert parse_json_field(None) is None


class TestJsonParam:
    """Tests for json_param function."""

    @pytest.mark.unit
    def test_returns_none_for_none(self):
        """None input should return None."""
        assert json_param(None) is None

    @pytest.mark.unit
    def test_serializes_dict(self):
        """Dict should be serialized to JSON string."""
        data = {"key": "value"}
        result = json_param(data)
        assert json.loads(result) == data

    @pytest.mark.unit
    def test_normalizes_then_serializes(self):
        """Should normalize double-encoded input before serializing."""
        inner = {"key": "value"}
        double_encoded = json.dumps(json.dumps(inner))
        result = json_param(double_encoded)
        # Should normalize first, then serialize
        assert json.loads(result) == inner
