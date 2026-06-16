"""Pytest fixtures and configuration."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def mock_db():
    """Mock database connection for tests that don't need real DB."""
    with patch("services.db.pg_query_one", new_callable=AsyncMock) as mock_query_one, \
         patch("services.db.pg_query_all", new_callable=AsyncMock) as mock_query_all, \
         patch("services.db.pg_execute", new_callable=AsyncMock) as mock_execute:
        yield {
            "query_one": mock_query_one,
            "query_all": mock_query_all,
            "execute": mock_execute,
        }


@pytest.fixture
def sample_preauth_payload():
    """Sample preauth webhook payload for testing."""
    return {
        "event_id": "evt_123",
        "event_type": "pa.submitted",
        "correlation_id": "corr_456",
        "checkin_id": "CHK001",
        "request_id": "REQ001",
        "patient_id": "PAT001",
        "enrollee": {
            "insurance_no": "INS123",
            "first_name": "John",
            "surname": "Doe",
        },
        "policy": {
            "plan_name": "Gold Plan",
            "insurance_package": "Premium",
        },
        "encounter": {
            "checkin_id": "CHK001",
            "facility_name": "General Hospital",
            "diagnosis": "Routine checkup",
        },
        "pa_items": [
            {
                "item_name": "Blood Test",
                "category_id": 3,
                "quantity": 1,
                "requested_cost": 5000,
                "status": 0,
            },
            {
                "item_name": "X-Ray",
                "category_id": 4,
                "quantity": 1,
                "requested_cost": 15000,
                "status": 0,
            },
        ],
        "total_requested_cost": 20000,
    }


@pytest.fixture
def sample_jwt_claims():
    """Sample JWT claims for authenticated requests."""
    return {
        "sub": "1",
        "email": "admin@example.com",
        "org_id": 1,
        "role": "admin",
    }
