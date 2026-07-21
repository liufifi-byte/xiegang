"""Privacy and untrusted-observation safeguards for agent tool use."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable

from app.agents.schemas import ToolObservation, ToolResultItem


_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EXACT_ADDRESS = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{2,}(?:路|街|巷|弄|大道)\s*\d{1,5}\s*号?"
)
_HTML_TAG = re.compile(r"<[^>]+>")
_UNTRUSTED_INSTRUCTION = re.compile(
    r"(?i)(ignore (?:all |any )?(?:previous|prior) instructions|"
    r"system prompt|developer message|忽略.{0,12}(?:指令|提示)|"
    r"系统提示词|执行以下命令|调用工具)"
)


def sanitize_query(query: str, sensitive_values: Iterable[str] = ()) -> str:
    """Remove known private values and common direct identifiers from a query."""
    result = query or ""
    for value in sorted(
        {str(v).strip() for v in sensitive_values if str(v).strip()},
        key=len,
        reverse=True,
    ):
        if len(value) >= 2:
            result = result.replace(value, "[已脱敏]")
    result = _EMAIL.sub("[邮箱已脱敏]", result)
    result = _PHONE.sub("[电话已脱敏]", result)
    result = _ID_CARD.sub("[证件已脱敏]", result)
    result = _EXACT_ADDRESS.sub("[地址已概括]", result)
    return re.sub(r"\s+", " ", result).strip()


def sanitize_public_error(error: Exception | str) -> str:
    """Return stable user-safe error copy without stack or credentials."""
    text = str(error).lower()
    if "timeout" in text or "timed out" in text:
        return "工具响应超时，专家将使用现有信息继续分析"
    return "工具暂时不可用，专家将使用现有信息继续分析"


def clean_untrusted_text(text: str, max_chars: int) -> str:
    """Normalize external content and neutralize obvious prompt-injection copy."""
    cleaned = html.unescape(text or "")
    cleaned = _HTML_TAG.sub(" ", cleaned)
    cleaned = _UNTRUSTED_INSTRUCTION.sub("[已过滤不可信网页指令]", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned


class ObservationNormalizer:
    def __init__(self, max_chars: int = 4000, max_items: int = 5):
        self.max_chars = max_chars
        self.max_items = max_items

    def normalize(self, observation: ToolObservation) -> ToolObservation:
        seen: set[tuple[str, str]] = set()
        normalized: list[ToolResultItem] = []
        remaining = self.max_chars
        for item in observation.results:
            if remaining <= 0 or len(normalized) >= self.max_items:
                break
            content = clean_untrusted_text(item.content, remaining)
            key = ((item.url or "").strip(), content[:160])
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                ToolResultItem(
                    title=clean_untrusted_text(item.title, 200),
                    url=item.url,
                    content=content,
                    score=item.score,
                )
            )
            remaining -= len(content)
        return observation.model_copy(update={"results": normalized})
