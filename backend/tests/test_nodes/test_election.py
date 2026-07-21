"""election 节点测试。"""
from app.graph.nodes.election import (
    _generate_pitches,
    _score_all_pitches,
    _score_pitch,
    _score_slot_a,
    _score_slot_b,
    _score_slot_c,
    run_election,
)
from app.schemas.member import ElectionPitch


def _mk_pitch(
    member_id="x",
    recommend_target="chen",
    target_view="老沉说 -83% 回撤是大风险需要分仓",
    target_blind="可能错过机会",
    own_bias="我的偏见是过度乐观会忽略尾部风险",
    synthesis="我会先列出共识再标记分歧最后给建议",
):
    """测试用工厂。"""
    return ElectionPitch(
        member_id=member_id,
        recommend_target=recommend_target,
        target_specific_view=target_view,
        target_blind_spot=target_blind,
        own_bias=own_bias,
        synthesis_method=synthesis,
    )


# ============ 评分规则单元测试 ============

def test_score_slot_a_long_quote():
    assert _score_slot_a("老沉说 -83% 回撤很可怕，需要分散风险") == 1


def test_score_slot_a_short_quote():
    assert _score_slot_a("他说好") == 0


def test_score_slot_a_empty():
    assert _score_slot_a("") == 0


def test_score_slot_b_with_bias_keyword():
    assert _score_slot_b("我的偏见是过度乐观，会通过反向论证抵消") == 1


def test_score_slot_b_without_self():
    assert _score_slot_b("偏见是普遍的") == 0  # 没有"我"


def test_score_slot_b_too_short():
    assert _score_slot_b("短") == 0


def test_score_slot_c_specific_method():
    assert _score_slot_c("我会先列出共识，再标记分歧，最后给出建议") == 1


def test_score_slot_c_vague():
    assert _score_slot_c("客观公正") == 0  # 太短


def test_score_pitch_full():
    assert _score_pitch(_mk_pitch()) == 3


def test_score_pitch_all_empty():
    pitch = ElectionPitch(
        member_id="x", recommend_target="y",
        target_specific_view="", target_blind_spot="",
        own_bias="", synthesis_method="",
    )
    assert _score_pitch(pitch) == 0


# ============ 节点级测试 ============

def test_score_all_pitches_handles_multiple():
    pitches = [
        _mk_pitch(member_id="suan"),
        _mk_pitch(member_id="chen", synthesis="短"),
        _mk_pitch(member_id="ji", own_bias=""),
    ]
    scores = _score_all_pitches(pitches)
    assert set(scores.keys()) == {"suan", "chen", "ji"}
    assert scores["suan"] == 3  # 完整
    assert scores["chen"] < 3    # synthesis 短，扣 1 分
    assert scores["ji"] < 3      # own_bias 空


def test_generate_pitches_returns_5(monkeypatch):
    from app.graph.nodes import election as el_module

    class MockLLM:
        def invoke(self, prompt):
            return _mk_pitch()

    monkeypatch.setattr(
        el_module, "get_structured_llm",
        lambda schema, temperature=0.5: MockLLM(),
    )

    state = {
        "topic": "买房",
        "members": {"suan": {"stance": "for", "confidence": 0.7, "reasoning_summary": "数字正"}},
    }
    pitches = _generate_pitches(state)
    assert len(pitches) == 5
    for p in pitches:
        assert p.recommend_target != p.member_id


def test_generate_pitches_forces_other_when_self_recommend(monkeypatch):
    """LLM 偶尔会推荐自己，节点要强制改成非自己。"""
    from app.graph.nodes import election as el_module

    class SelfRecommendingLLM:
        def __init__(self):
            self.count = 0
        def invoke(self, prompt):
            self.count += 1
            mid = ["suan", "chen", "ji", "nian", "fan"][self.count - 1]
            return ElectionPitch(
                member_id=mid, recommend_target=mid,  # 推荐自己
                target_specific_view="我说的很具体xxxxxxxxxxxxx",
                target_blind_spot="我有盲区xxxxxx",
                own_bias="我的偏见是xxxxx",
                synthesis_method="我会先做某事xxxxxx",
            )

    monkeypatch.setattr(
        el_module, "get_structured_llm",
        lambda schema, temperature=0.5: SelfRecommendingLLM(),
    )

    state = {"topic": "x", "members": {}}
    pitches = _generate_pitches(state)
    # 关键断言：不允许任何议员推荐自己
    for p in pitches:
        assert p.recommend_target != p.member_id, (
            f"{p.member_id} 推荐了自己 {p.recommend_target}"
        )


def test_run_election_picks_winner_when_concentrated(monkeypatch):
    """5 个议员都投 chen → chen 当选，非兜底。"""
    from app.graph.nodes import election as el_module

    def mock_generate(state):
        return [
            _mk_pitch(member_id=mid, recommend_target="chen")
            for mid in ["suan", "chen", "ji", "nian", "fan"]
        ]

    monkeypatch.setattr(el_module, "_generate_pitches", mock_generate)
    state = {"topic": "买房", "members": {}}
    result = run_election(state)
    assert result["election_result"]["winner"] == "chen"
    assert result["election_result"]["fallback_used"] is False


def test_run_election_fallback_when_dispersed(monkeypatch):
    """5 个议员各投不同人 → 票太分散 → fallback 到反对。"""
    from app.graph.nodes import election as el_module

    def mock_generate(state):
        targets = {"suan": "chen", "chen": "ji", "ji": "nian",
                   "nian": "fan", "fan": "suan"}
        return [
            _mk_pitch(member_id=mid, recommend_target=targets[mid])
            for mid in ["suan", "chen", "ji", "nian", "fan"]
        ]

    monkeypatch.setattr(el_module, "_generate_pitches", mock_generate)
    state = {"topic": "买房", "members": {}}
    result = run_election(state)
    assert result["election_result"]["winner"] == "fan"
    assert result["election_result"]["fallback_used"] is True
    assert "30%" in result["election_result"]["fallback_reason"]


def test_run_election_fallback_when_all_zero_scores(monkeypatch):
    """所有评分 = 0（发言全空话）→ fallback。"""
    from app.graph.nodes import election as el_module

    def mock_generate(state):
        return [
            _mk_pitch(
                member_id=mid,
                target_view="",
                target_blind="",
                own_bias="",
                synthesis="",
            )
            for mid in ["suan", "chen", "ji", "nian", "fan"]
        ]

    monkeypatch.setattr(el_module, "_generate_pitches", mock_generate)
    state = {"topic": "x", "members": {}}
    result = run_election(state)
    assert result["election_result"]["winner"] == "fan"
    assert result["election_result"]["fallback_used"] is True
    assert "评分均为 0" in result["election_result"]["fallback_reason"]


def test_run_election_writes_to_transcript(monkeypatch):
    """transcript 应该记录选举结果。"""
    from app.graph.nodes import election as el_module

    def mock_generate(state):
        return [
            _mk_pitch(member_id=mid, recommend_target="chen")
            for mid in ["suan", "chen", "ji", "nian", "fan"]
        ]

    monkeypatch.setattr(el_module, "_generate_pitches", mock_generate)
    state = {"topic": "x", "members": {}, "transcript": [{"type": "debate"}]}
    result = run_election(state)
    assert any(e["type"] == "debate" for e in result["transcript"])
    election_entries = [e for e in result["transcript"] if e["type"] == "election"]
    assert len(election_entries) == 1
    assert "chen" in election_entries[0]["content"]


def test_run_election_accepts_dynamic_professional_ids(monkeypatch):
    from app.graph.nodes import election as el_module

    selected = [
        "mathematician", "economist", "psychologist", "artist", "politician"
    ]

    def mock_generate(state):
        targets = {
            "mathematician": "economist",
            "economist": "politician",
            "psychologist": "politician",
            "artist": "politician",
            "politician": "economist",
        }
        return [
            _mk_pitch(member_id=mid, recommend_target=targets[mid])
            for mid in selected
        ]

    monkeypatch.setattr(el_module, "_generate_pitches", mock_generate)
    state = {
        "topic": "是否接 Offer",
        "members": {},
        "selected_expert_ids": selected,
        "debate_duties": {"psychologist": "contrarian_challenge"},
    }
    result = run_election(state)
    assert result["election_result"]["winner"] in selected
    assert set(result["election_result"]["vote_tally"]) <= set(selected)
