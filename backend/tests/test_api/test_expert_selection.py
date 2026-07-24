def test_fixed_scene_preview_is_deterministic(client):
    response = client.post(
        "/api/expert-selection/preview",
        json={
            "topic": "现在买房还是再等一年？",
            "topic_category": "real_estate",
            "user_background": "预算 300 万，自住",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["selection_source"] == "category_rule"
    assert data["recommended_expert_ids"] == [
        "mathematician",
        "economist",
        "psychologist",
        "historian",
        "politician",
    ]
    assert set(data["reasons"]) == set(data["recommended_expert_ids"])
    assert "现金流" in data["coverage"]


def test_general_preview_uses_topic_rule(client):
    response = client.post(
        "/api/expert-selection/preview",
        json={
            "topic": "要不要和伴侣结婚，双方家庭矛盾很大？",
            "topic_category": "general",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["selection_source"] == "topic_rule"
    assert len(data["recommended_expert_ids"]) == 5
    assert "psychologist" in data["recommended_expert_ids"]


def test_preview_rejects_empty_topic(client):
    response = client.post(
        "/api/expert-selection/preview",
        json={"topic": "", "topic_category": "general"},
    )
    assert response.status_code == 422
