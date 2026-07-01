"""Health endpoint tests for the minimal mdshare API."""


class TestHealth:
    """GET /api/health — always returns 200 with status ok."""

    def test_health_returns_200_and_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.is_json
        data = response.get_json()
        assert data["status"] == "ok"

    def test_health_response_is_json(self, client):
        response = client.get("/api/health")
        assert response.content_type == "application/json"
