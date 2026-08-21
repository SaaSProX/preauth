"""Tests for health check endpoint."""

import pytest


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @pytest.mark.unit
    def test_health_returns_ok(self):
        """Health endpoint should return status ok."""
        # Import here to avoid import errors if dependencies missing
        from fastapi.testclient import TestClient
        from main import app
        
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.unit
    def test_health_is_fast(self):
        """Health endpoint should respond quickly."""
        import time
        from fastapi.testclient import TestClient
        from main import app
        
        client = TestClient(app)
        
        start = time.time()
        response = client.get("/health")
        elapsed = time.time() - start
        
        assert response.status_code == 200
        assert elapsed < 1.0  # Should respond in under 1 second
