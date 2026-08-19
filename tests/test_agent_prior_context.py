"""Tests for AMAN prior-decision context helpers in agent/agent.py.

These back the "mirror AMAN's decision instead of escalating" fast path
(SAA-mirror-preapproved fix) — the bug we shipped fixed a case where a PA
with some AMAN-rejected lines and some AMAN-approved lines was mislabeled
as a blanket APPROVE. These tests pin that behavior down.
"""

import pytest

from agent.agent import (
    _aman_prior_context,
    _current_submission_items,
    _item_approved_cost,
    _item_cost,
    _item_status,
)


def _item(status, requested_cost=1000, approved_cost=None, **overrides):
    data = {
        "claim_item_id": overrides.pop("claim_item_id", "CI1"),
        "item_name": overrides.pop("item_name", "Test Item"),
        "requested_cost": requested_cost,
        "status": status,
    }
    if approved_cost is not None:
        data["approved_cost"] = approved_cost
    data.update(overrides)
    return data


class TestItemStatus:
    """_item_status normalizes both string labels and AMAN's numeric codes."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("pending", "pending"),
            ("Approved", "approved"),
            ("REJECTED", "rejected"),
            (0, "pending"),
            (1, "approved"),
            (2, "queried"),
            (3, "rejected"),
            (99, "unknown"),
            (None, "unknown"),
        ],
    )
    def test_status_normalization(self, value, expected):
        item = {"status": value}
        assert _item_status(item) == expected

    @pytest.mark.unit
    def test_item_status_label_takes_precedence(self):
        item = {"item_status_label": "approved", "status": 3}
        assert _item_status(item) == "approved"


class TestItemCost:
    @pytest.mark.unit
    def test_prefers_requested_cost(self):
        assert _item_cost({"requested_cost": 5000}) == 5000

    @pytest.mark.unit
    def test_falls_back_to_unit_cost_times_quantity(self):
        assert _item_cost({"unit_cost": 100, "quantity": 3}) == 300

    @pytest.mark.unit
    def test_defaults_to_zero(self):
        assert _item_cost({}) == 0.0


class TestItemApprovedCost:
    @pytest.mark.unit
    def test_uses_approved_cost_when_positive(self):
        assert _item_approved_cost({"approved_cost": 4000, "requested_cost": 5000}) == 4000

    @pytest.mark.unit
    def test_falls_back_to_requested_cost_when_approved_cost_missing(self):
        assert _item_approved_cost({"requested_cost": 5000}) == 5000

    @pytest.mark.unit
    def test_falls_back_to_requested_cost_when_approved_cost_zero(self):
        # A zero approved_cost isn't a real signal (AMAN hasn't priced it yet) -
        # fall back to the requested amount rather than reporting ₦0.
        assert _item_approved_cost({"approved_cost": 0, "requested_cost": 5000}) == 5000


class TestAmanPriorContext:
    """The core logic our fix corrected: don't collapse mixed outcomes into APPROVE."""

    @pytest.mark.unit
    def test_all_approved_counts_correctly(self):
        pa = {
            "items": [
                _item("approved", requested_cost=1000, approved_cost=1000, claim_item_id="A"),
                _item("approved", requested_cost=2000, approved_cost=1800, claim_item_id="B"),
            ]
        }
        ctx = _aman_prior_context(pa, current_items=[])
        assert ctx["approved_count"] == 2
        assert ctx["rejected_count"] == 0
        assert ctx["approved_amount"] == 2800

    @pytest.mark.unit
    def test_mixed_approved_and_rejected_is_not_collapsed_to_approve(self):
        """Regression test for the bug in the original webhook fast-path:
        it checked `if approved` before `if rejected`, so any PA with at
        least one approved line and one rejected line was mislabeled as a
        full APPROVE. approved_count and rejected_count must both be
        reported so callers can tell mixed outcomes apart.
        """
        pa = {
            "items": [
                _item("approved", requested_cost=1000, approved_cost=1000, claim_item_id="A"),
                _item("rejected", requested_cost=3000, claim_item_id="B"),
            ]
        }
        ctx = _aman_prior_context(pa, current_items=[])
        assert ctx["approved_count"] == 1
        assert ctx["rejected_count"] == 1
        assert ctx["approved_amount"] == 1000
        assert ctx["rejected_requested_amount"] == 3000
        # A caller checking "everything is approved" must require BOTH:
        # approved_count == total AND rejected_count == 0.
        is_clean_approve = ctx["rejected_count"] == 0 and ctx["approved_count"] == 2
        assert is_clean_approve is False

    @pytest.mark.unit
    def test_all_rejected(self):
        pa = {
            "items": [
                _item("rejected", requested_cost=1500, claim_item_id="A"),
                _item("rejected", requested_cost=2500, claim_item_id="B"),
            ]
        }
        ctx = _aman_prior_context(pa, current_items=[])
        assert ctx["approved_count"] == 0
        assert ctx["rejected_count"] == 2
        assert ctx["rejected_requested_amount"] == 4000

    @pytest.mark.unit
    def test_pending_items_excluded_from_prior_context(self):
        # Pending items are the *current* submission, not prior history -
        # they must not be counted as approved or rejected.
        pending = _item("pending", requested_cost=1000, claim_item_id="P")
        approved = _item("approved", requested_cost=2000, approved_cost=2000, claim_item_id="A")
        pa = {"items": [pending, approved]}
        ctx = _aman_prior_context(pa, current_items=[pending])
        assert ctx["approved_count"] == 1
        assert ctx["rejected_count"] == 0

    @pytest.mark.unit
    def test_empty_items_returns_zeroed_context(self):
        ctx = _aman_prior_context({"items": []}, current_items=[])
        assert ctx["approved_count"] == 0
        assert ctx["rejected_count"] == 0
        assert ctx["approved_amount"] == 0
        assert ctx["approved_items"] == []
        assert ctx["rejected_items"] == []

    @pytest.mark.unit
    def test_queried_items_are_neither_approved_nor_rejected(self):
        queried = _item("queried", requested_cost=1000, claim_item_id="Q")
        pa = {"items": [queried]}
        ctx = _aman_prior_context(pa, current_items=[])
        assert ctx["approved_count"] == 0
        assert ctx["rejected_count"] == 0


class TestCurrentSubmissionItems:
    @pytest.mark.unit
    def test_returns_only_pending_items_when_no_items_added(self):
        pending = _item("pending", claim_item_id="P")
        approved = _item("approved", claim_item_id="A")
        pa = {"items": [pending, approved]}
        result = _current_submission_items(pa)
        assert result == [pending]

    @pytest.mark.unit
    def test_no_pending_items_returns_empty_list(self):
        approved = _item("approved", claim_item_id="A")
        pa = {"items": [approved]}
        assert _current_submission_items(pa) == []
