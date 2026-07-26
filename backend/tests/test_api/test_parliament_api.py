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


def test_parliament_create_persists_confirmed_experts(client, monkeypatch):
    from app.api import parliament as parl_api

    inserted = {}

    class MockSupabase:
        def table(self, name):
            class Table:
                def insert(self, data):
                    inserted.update(data)

                    class Exec:
                        def execute(self_inner):
                            class Result:
                                data = [{"id": "confirmed-roster"}]

                            return Result()

                    return Exec()

            return Table()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())
    selected = ["mathematician", "economist", "psychologist"]
    response = client.post(
        "/api/parliament",
        json={
            "topic": "是否换工作",
            "selected_expert_ids": selected,
        },
    )
    assert response.status_code == 200
    assert inserted["selected_expert_ids"] == selected


def test_parliament_create_rejects_invalid_rosters(client):
    for selected in (
        ["mathematician", "economist"],
        [
            "mathematician",
            "economist",
            "psychologist",
            "historian",
            "artist",
            "politician",
        ],
        ["mathematician", "economist", "unknown"],
        ["mathematician", "economist", "economist"],
    ):
        response = client.post(
            "/api/parliament",
            json={"topic": "测试", "selected_expert_ids": selected},
        )
        assert response.status_code == 422


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


def test_parliament_get_returns_archived_agent_research(client, monkeypatch):
    from app.api import parliament as parl_api

    class MockSupabase:
        def table(self, name):
            class Chain:
                def select(self, *args):
                    return self
                def eq(self, *args):
                    return self
                def single(self):
                    return self
                def execute(self):
                    class Result:
                        data = {
                            "id": "p1",
                            "topic": "Offer",
                            "status": "done",
                            "created_at": "2026-07-21T00:00:00Z",
                            "summary": {
                                "selected_expert_ids": ["economist"],
                                "agent_results": {"economist": {"result": {"thesis": "x"}}},
                            },
                            "transcript": [],
                            "members_state": {"economist": {"stance": "for"}},
                            "tool_calls": [
                                {
                                    "action": "agent_trace",
                                    "trace": {"agent_id": "economist", "phase": "started"},
                                }
                            ],
                        }
                    return Result()
            return Chain()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())
    response = client.get("/api/parliament/p1")
    assert response.status_code == 200
    data = response.json()
    assert data["selected_expert_ids"] == ["economist"]
    assert data["agent_traces"][0]["phase"] == "started"


def test_summary_query_can_exclude_agent_work_path(client, monkeypatch):
    from app.api import parliament as parl_api

    class MockSupabase:
        def table(self, name):
            class Chain:
                def select(self, *args):
                    return self
                def eq(self, *args):
                    return self
                def single(self):
                    return self
                def execute(self):
                    class Result:
                        data = {
                            "id": "p-summary",
                            "topic": "买房",
                            "status": "done",
                            "created_at": "2026-07-26T00:00:00Z",
                            "summary": {"advice": "暂缓"},
                            "transcript": [],
                            "members_state": {},
                            "tool_calls": [
                                {
                                    "action": "agent_trace",
                                    "trace": {
                                        "agent_id": "economist",
                                        "phase": "tool_started",
                                    },
                                }
                            ],
                        }

                    return Result()

            return Chain()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())
    response = client.get(
        "/api/parliament/p-summary?include_agent_traces=false"
    )
    assert response.status_code == 200
    assert response.json()["agent_traces"] == []
