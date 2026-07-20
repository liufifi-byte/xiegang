"""议员相关 Pydantic schemas。"""
from typing import Literal

from pydantic import BaseModel, Field


Stance = Literal["for", "against", "neutral"]
EvidenceStrength = Literal["high", "medium", "low"]


class Evidence(BaseModel):
    claim: str
    source: str
    strength: EvidenceStrength


class MemberStatement(BaseModel):
    """单一议员一轮发言的结构化输出。"""
    member_id: str
    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list, max_length=5)
    reasoning_summary: str


class ElectionPitch(BaseModel):
    """议长选举：议员的三槽位发言。

    槽位 A：推荐某人（必须引用对方具体观点 + 指出对方盲区）
    槽位 B：自证偏见
    槽位 C：综合方法
    """
    member_id: str
    recommend_target: str = Field(description="被推荐的议员 ID（不能是自己）")
    target_specific_view: str = Field(description="被推荐者的具体观点引用")
    target_blind_spot: str = Field(description="被推荐者的盲区")
    own_bias: str = Field(description="自证偏见")
    synthesis_method: str = Field(description="综合方法")
