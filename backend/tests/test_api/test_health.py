def test_health_endpoint_returns_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "council-backend"}


def test_root_returns_message(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "邪杠" in data["message"]
    assert data["env"] in ("development", "production", "test")
