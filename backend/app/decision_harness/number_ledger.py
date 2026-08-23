"""统一数字账本（2026-08-23 评审驱动：跨 Agent/跨轮数字矛盾的根治）。

问题实录：经济学家第二轮"零涨幅 5 年亏损超 200 万"与它自己第一轮
参数（年净资本成本约 20 万 × 5 年 = 100 万量级）矛盾；数学家把
"家庭年薪 60 万"直接当"税后月收入 5 万"——口径漂移无人记账。

机制：所有 Agent 报出的关键数字（收入/利率/月供/机会成本/租金…）
进同一本账；同标签不同值 = 数字冲突（可升级为对账争议）；辩论包与
终局包强制注入账本快照——引用必须一致，改数必须显式声明。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 数字标签归一化：同义说法折叠到同一键（"月供"="每月还款额"）
_LABEL_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("税后月收入", "月收入", "家庭月收入", "可支配月收入"), "税后月收入"),
    (("税前年薪", "家庭年薪", "年薪", "家庭年收入"), "税前年薪"),
    (("月供", "每月还款额", "月还款"), "月供"),
    (("月供占收入比", "月供占比"), "月供占收入比"),
    (("贷款利率", "利率", "商贷利率"), "贷款利率"),
    (("公积金额度", "公积金贷款额度"), "公积金贷款额度"),
    (("机会成本", "年化机会成本", "首付机会成本"), "年化机会成本"),
    (("年租金", "租金", "年租金节省", "租金节省"), "年租金"),
    (("贷款本金", "贷款总额", "贷款额"), "贷款本金"),
    (("交易成本", "交易税费"), "交易成本"),
    (("持有年限阈值", "最小持有年限", "持有期限阈值"), "最小持有年限"),
]

_VALID_BASIS = ("user_input", "assumption", "calculation", "evidence")


def normalize_label(raw: str) -> str:
    text = str(raw or "").strip()
    for aliases, canonical in _LABEL_ALIASES:
        if text in aliases:
            return canonical
    # 包含匹配（"家庭税后月收入"→"税后月收入"）
    for aliases, canonical in _LABEL_ALIASES:
        if any(a in text for a in aliases):
            return canonical
    return text[:20] or "未命名数字"


def _norm_value(value: Any) -> str:
    """数值归一化比较：'2.1万'/'21000'/21000 → 同一可比串。"""
    if isinstance(value, (int, float)):
        return f"{float(value):.4g}"
    text = str(value or "").strip().replace(",", "").replace("，", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(万|亿|%)?", text)
    if not m:
        return text[:12]
    num = float(m.group(1))
    unit = m.group(2) or ""
    if unit == "万":
        num *= 10000
    elif unit == "亿":
        num *= 100000000
    return f"{num:.4g}"


@dataclass
class NumberEntry:
    label: str                 # 归一化标签
    raw_value: str             # 原始值（展示）
    norm_value: str            # 归一化比较值
    basis: str                 # user_input | assumption | calculation | evidence
    source_agent: str
    round_no: int = 0
    note: str = ""             # 口径说明（税前/税后、首套/二套…）

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "value": self.raw_value, "basis": self.basis,
            "source": self.source_agent, "round": self.round_no, "note": self.note,
        }


@dataclass
class LedgerConflict:
    label: str
    values: list[NumberEntry]

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "entries": [e.to_dict() for e in self.values]}


@dataclass
class NumberLedger:
    entries: list[NumberEntry] = field(default_factory=list)
    conflicts: list[LedgerConflict] = field(default_factory=list)
    resolved_labels: set[str] = field(default_factory=set)

    # ---- 写入 ----

    def register(self, label: str, value: Any, *, basis: str,
                 source_agent: str, round_no: int = 0, note: str = "") -> None:
        canonical = normalize_label(label)
        basis = basis if basis in _VALID_BASIS else "assumption"
        entry = NumberEntry(
            label=canonical, raw_value=str(value),
            norm_value=_norm_value(value), basis=basis,
            source_agent=source_agent, round_no=round_no, note=note[:60],
        )
        same = [e for e in self.entries if e.label == canonical]
        differing = [e for e in same if e.norm_value != entry.norm_value]
        if differing and canonical not in self.resolved_labels:
            existing = self._find_conflict(canonical)
            bucket = existing.values if existing else differing
            if entry not in bucket:
                bucket.append(entry)
            if existing is None:
                self.conflicts.append(LedgerConflict(canonical, bucket))
        elif not same or not differing:
            self.entries.append(entry)

    def mark_resolved(self, label: str) -> None:
        self.resolved_labels.add(normalize_label(label))

    def _find_conflict(self, label: str) -> LedgerConflict | None:
        for c in self.conflicts:
            if c.label == label:
                return c
        return None

    # ---- 读取 ----

    def unresolved_conflicts(self) -> list[LedgerConflict]:
        return [c for c in self.conflicts if c.label not in self.resolved_labels]

    def get(self, label: str) -> NumberEntry | None:
        canonical = normalize_label(label)
        candidates = [e for e in self.entries if e.label == canonical]
        return candidates[-1] if candidates else None

    def snapshot_for_prompt(self) -> str:
        """注入辩论/终局 prompt 的公认数字表。"""
        if not self.entries:
            return "（尚无公认数字）"
        seen: dict[str, NumberEntry] = {}
        for e in self.entries:
            if e.label not in seen:
                seen[e.label] = e
        lines = [
            f"- {e.label} = {e.raw_value}"
            f"（依据：{ {'user_input': '用户输入', 'assumption': '假设',
                        'calculation': '计算', 'evidence': '证据'}[e.basis] }"
            + (f"；口径：{e.note}" if e.note else "") + "）"
            for e in seen.values()
        ]
        text = "【会议公认数字表——引用必须与此一致；要改数必须显式声明并给理由】\n" + "\n".join(lines)
        if self.unresolved_conflicts():
            text += "\n【数字冲突待对账】" + "；".join(
                f"{c.label}：" + " vs ".join(
                    f"{e.raw_value}({e.source_agent})" for e in c.values[:3]
                ) for c in self.unresolved_conflicts()[:4]
            )
        return text

    # ---- 持久化 ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "resolved": sorted(self.resolved_labels),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "NumberLedger":
        ledger = cls()
        for item in (data or {}).get("entries", []):
            ledger.entries.append(NumberEntry(
                label=item["label"], raw_value=item["value"],
                norm_value=_norm_value(item["value"]), basis=item.get("basis", "assumption"),
                source_agent=item.get("source", "?"), round_no=item.get("round", 0),
                note=item.get("note", ""),
            ))
        for c in (data or {}).get("conflicts", []):
            values = [e for e in ledger.entries
                      if e.label == c.get("label") and e.to_dict() in c.get("entries", [])]
            if not values:
                values = [e for e in ledger.entries if e.label == c.get("label")][:2]
            ledger.conflicts.append(LedgerConflict(c.get("label", "?"), values))
        ledger.resolved_labels = set((data or {}).get("resolved", []))
        return ledger


def collect_from_results(results: list[Any], *, round_no: int = 1) -> NumberLedger:
    """从 SubAgentResult.numbers（宽松 dict 列表）汇账。"""
    ledger = NumberLedger()
    for r in results:
        for item in (getattr(r, "numbers", None) or [])[:10]:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or item.get("name") or ""
            value = item.get("value")
            if not label or value is None:
                continue
            ledger.register(
                label, value,
                basis=str(item.get("basis") or "assumption"),
                source_agent=getattr(r, "agent_id", "?"),
                round_no=round_no,
                note=str(item.get("note") or item.get("口径") or ""),
            )
    return ledger
