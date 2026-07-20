"""parliament HTTP API 测试。"""
def test_parliament_create_endpoint_returns_id(client, monkeypatch):
    from app.api import parliament as parl_api

    class MockSupabase:
        def table(self, name):
            class Table:
                def insert(self, data):
                    class Result:
                        data = [{"id": "test-parliament-id"}]
                    class Exec:
                        def execute(self_inner):
                            return Result()
                    return Exec()
            return Table()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())

    response = client.post("/api/parliament", json={
        "topic": "买房",
        "topic_category": "real_estate",
        "user_background": "深圳工作 5 年",
    })
    assert response.status_code == 200
    data = response.json()
    assert "parliament_id" in data
    assert "stream_url" in data
    assert data["parliament_id"] == "test-parliament-id"


def test_parliament_get_returns_404_for_missing(client, monkeypatch):
    from app.api import parliament as parl_api

    class MockSupabase:
        def table(self, name):
            class Table:
                def select(self, *args):
                    return Chain()
            class Chain:
                def eq(self, *args, **kwargs):
                    return self
                def single(self):
                    class Result:
                        data = None
                    class Exec:
                        def execute(self_inner):
                            return Result()
                    return Exec()
            return Table()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())

    response = client.get("/api/parliament/non-existent-id")
    assert response.status_code == 404


def test_list_parliaments_returns_items(client, monkeypatch):
    from app.api import parliament as parl_api

    class MockSupabase:
        def table(self, name):
            class Chain:
                def select(self, *args):
                    return self
                def order(self, *args, **kwargs):
                    return self
                def limit(self, *args):
                    class Result:
                        data = [
                            {"id": "p1", "topic": "买房", "status": "done"},
                            {"id": "p2", "topic": "跳槽", "status": "done"},
                        ]
                    class Exec:
                        def execute(self_inner):
                            return Result()
                    return Exec()
            return Chain()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())

    response = client.get("/api/parliament/")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["topic"] == "买房"
