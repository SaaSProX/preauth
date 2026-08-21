"""Tests for pure decision-normalization helpers behind the QA accuracy
dashboard in auth/router.py (SAA-52)."""

import pytest

from auth.router import _qa_norm_dec


class TestQaNormDec:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("approve", "APPROVE"),
            ("Approved", "APPROVE"),
            ("APPROVE", "APPROVE"),
            ("deny", "DENY"),
            ("denied", "DENY"),
            ("reject", "DENY"),
            ("Rejected", "DENY"),
        ],
    )
    def test_normalizes_known_variants(self, value, expected):
        assert _qa_norm_dec(value) == expected

    @pytest.mark.unit
    def test_escalate_normalizes_to_none(self):
        # ESCALATE is deliberately "no firm verdict" -> None, which the QA
        # dashboard classifies as agent_skipped rather than a real decision.
        assert _qa_norm_dec("escalate") is None
        assert _qa_norm_dec("ESCALATED") is None

    @pytest.mark.unit
    @pytest.mark.parametrize("value", [None, "", "unknown", "pending", "  "])
    def test_unrecognized_or_empty_values_normalize_to_none(self, value):
        assert _qa_norm_dec(value) is None

    @pytest.mark.unit
    def test_whitespace_is_trimmed(self):
        assert _qa_norm_dec("  approve  ") == "APPROVE"

    @pytest.mark.unit
    def test_case_insensitive(self):
        assert _qa_norm_dec("aPpRoVe") == "APPROVE"
        assert _qa_norm_dec("dEnY") == "DENY"
