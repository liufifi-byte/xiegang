"""GLM-5 (智谱) LLM 客户端。

通过 langchain_openai.ChatOpenAI + OpenAI 兼容协议接入智谱。

关键工作：get_structured_llm 包了一层容错 wrapper——
GLM-5 通过 OpenAI 兼容协议时，with_structured_output 偶尔会因为模型输出
```json ... ``` markdown fence 而失败。本 wrapper 在原生 structured 失败时
降级到普通 invoke + 手动 JSON 提取 + 重新校验。

P0 修订（重试风暴防护）：仅当底层 LLM 调用真的成功、只是结构化输出层失败时，
才允许"重用已返回的原文"重新解析；若失败属于限流（1302/1305/429）/ 网络 /
超时，绝不立即二次调用，直接抛出，避免在限流场景下补发请求进一步加重负载。
"""
import json
import re
from typing import TypeVar

from pydantic import BaseModel

from app.config import get_settings


T = TypeVar("T", bound=BaseModel)


# 限流 / 平台过载 / 速率限制类错误特征。匹配到这些 → 视为"不可立即重试"。
# 智谱错误码：1302 = 账户并发上限、1305 = 平台过载；
# HTTP 429 = 通用速率限制。
_RATE_LIMIT_MARKERS = (
    "1302", "1305", "429",
    "rate limit", "rate_limit", "ratelimit",
    "too many requests", "throttl",
    "overloaded", "quota",
)


def _is_rate_limit_error(err: BaseException) -> bool:
    """判断异常是否属于限流/平台过载类（不可立即重试）。

    用字符串匹配覆盖 OpenAI SDK / httpx / 智谱原始码等多来源，
    简单但保守，宁可错杀也不让限流场景下补发请求。
    """
    text = (str(err) or "").lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def get_llm(
    temperature: float = 0.7,
    max_tokens: int = 2000,
    *,
    fast: bool = False,
):
    """返回配置好的 GLM ChatOpenAI 客户端。

    fast=True 用延迟敏感轮次（辩论/选举/总结/会前流程）的快速模型
    （settings.glm_fast_model，默认 glm-5-turbo）并默认关闭深度思考：
    实测辩论类调用从 ~28s 降到 ~2s。研究轮保持 glm-5 开思考。
    """
    # 延迟 import：避免在 module import 时就创建客户端（测试环境用不上）
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    extra_kwargs = {}
    if fast and settings.glm_fast_disable_thinking:
        # 智谱 API 的思考开关（openai 兼容层走 extra_body 透传）
        extra_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(
        model=settings.glm_fast_model if fast else settings.glm_model,
        openai_api_key=settings.glm_api_key,
        openai_api_base=settings.glm_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=settings.glm_request_timeout_seconds,
        # 重要：max_retries 由 langchain SDK 内部处理，仅对网络/连接类错误重试，
        # 不影响本文件 _StructuredWrapper 的"立即二次调用"行为。
        max_retries=settings.glm_max_retries,
        **extra_kwargs,
    )


def get_fast_llm(temperature: float = 0.7, max_tokens: int = 2000):
    """辩论/选举/总结等延迟敏感轮次的快捷入口（fast 模型 + 关思考）。"""
    return get_llm(temperature=temperature, max_tokens=max_tokens, fast=True)


def extract_json_from_text(text: str) -> str:
    """从 LLM 输出中抠出 JSON 字符串。

    依次尝试：
      1. 剥 markdown fence ```json ... ``` 或 ``` ... ```
      2. 抽第一个 { 到最后一个 }

    Raises:
        ValueError: 找不到 JSON 对象时
    """
    text = (text or "").strip()

    # 1. 剥 markdown fence
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 2. 抽 { 到 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"无法在响应中找到 JSON 对象：{text[:120]!r}")

    return text[start:end + 1]


class _StructuredWrapper:
    """对 with_structured_output 的容错包装。

    工作流程：
      1. fast=False：先试原生 with_structured_output（走 function calling）。
         fast=True：用 bind_tools + 强制 tool_choice（模型必须调工具，
         不存在"回散文不调工具"的分支）。
      2. 仅当 (a) 异常是"纯结构化层失败"（底层 LLM 已成功返回、只是结构不合规）
         且 (b) 不是限流类异常时，才用普通 invoke 调一次 + 手动剥 fence + parse。
         注意：原生路径返回 None（部分 langchain-openai 版本在模型不调工具时
         不抛异常而返回 None，议会 7e8f068e 线上 11/13 辩论 AttributeError 实锤）
         同样视为结构化层失败，进入 fallback。
      3. 限流 / 网络 / 超时类异常 → 不二次调用，直接抛出，由上层兜底。
      4. 二次 parse 仍失败 → 抛融合异常（含两次错误信息）。
    """

    def __init__(self, schema: type[T], temperature: float, fast: bool = False):
        self.schema = schema
        self.fast = fast
        self.llm = get_llm(temperature=temperature, fast=fast)
        if fast:
            # 事故修复（议会 7e8f068e）：服务器侧 langchain 的
            # with_structured_output(method=function_calling) 在模型不调工具时
            # 返回 None 而非抛异常 → 上游 stmt.stance 直接 AttributeError。
            # 改用强制 tool_choice 的 bind_tools（NativeToolDecisionAdapter
            # 已在生产验证的模式），args 手动 model_validate。
            tool = {
                "type": "function",
                "function": {
                    "name": schema.__name__,
                    "description": (schema.__doc__ or "").strip()[:200]
                    or f"返回 {schema.__name__} 结构化数据",
                    "parameters": schema.model_json_schema(),
                },
            }
            self.forced = self.llm.bind_tools([tool], tool_choice=schema.__name__)
        else:
            self.forced = None
        self.structured = self.llm.with_structured_output(schema)

    def _parse_forced(self, response) -> T | None:
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return None
        first = tool_calls[0]
        args = first.get("args") if isinstance(first, dict) else first.args
        return self.schema.model_validate(args or {})

    def invoke(self, prompt, **kwargs):
        structured_err: BaseException | None = None
        if self.fast:
            try:
                parsed = self._parse_forced(self.forced.invoke(prompt, **kwargs))
                if parsed is not None:
                    return parsed
                structured_err = ValueError("forced tool call missing tool_calls")
            except Exception as err:
                if _is_rate_limit_error(err):
                    raise
                structured_err = err
        else:
            try:
                result = self.structured.invoke(prompt, **kwargs)
                if result is not None:
                    return result
                structured_err = ValueError("structured output returned None")
            except Exception as err:
                if _is_rate_limit_error(err):
                    raise
                structured_err = err
        # 结构化层失败（异常或 None）→ 普通 invoke 抓原文再 parse
        response = self.llm.invoke(prompt, **kwargs)
        content = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )
        json_str = extract_json_from_text(content)
        data = json.loads(json_str)
        try:
            return self.schema.model_validate(data)
        except Exception as validate_err:
            # 同时记录两个错误信息，方便调试
            raise ValueError(
                f"structured output fallback failed. "
                f"original={structured_err!r}, "
                f"validate={validate_err!r}, "
                f"raw_content[:200]={content[:200]!r}"
            ) from structured_err

    async def ainvoke(self, prompt, **kwargs):
        """V2-S2: 异步版本——原生 structured.ainvoke / llm.ainvoke。

        真异步路径，让 asyncio.gather 5 议员并发真正不阻塞事件循环。
        失败策略与同步版一致：限流类异常不立即补发，结构化层失败才重调。
        """
        structured_err: BaseException | None = None
        if self.fast:
            try:
                parsed = self._parse_forced(await self.forced.ainvoke(prompt, **kwargs))
                if parsed is not None:
                    return parsed
                structured_err = ValueError("forced tool call missing tool_calls")
            except Exception as err:
                if _is_rate_limit_error(err):
                    raise
                structured_err = err
        else:
            try:
                result = await self.structured.ainvoke(prompt, **kwargs)
                if result is not None:
                    return result
                structured_err = ValueError("structured output returned None")
            except Exception as err:
                if _is_rate_limit_error(err):
                    raise
                structured_err = err
        response = await self.llm.ainvoke(prompt, **kwargs)
        content = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )
        json_str = extract_json_from_text(content)
        data = json.loads(json_str)
        try:
            return self.schema.model_validate(data)
        except Exception as validate_err:
            raise ValueError(
                f"structured ainvoke fallback failed. "
                f"original={structured_err!r}, "
                f"validate={validate_err!r}, "
                f"raw_content[:200]={content[:200]!r}"
            ) from structured_err


def get_structured_llm(
    schema: type[BaseModel],
    temperature: float = 0.5,
    *,
    fast: bool = False,
):
    """返回容错的结构化输出 LLM。

    返回对象支持 `.invoke(prompt)` 同步 和 `.ainvoke(prompt)` 异步接口，
    返回 Pydantic schema 实例。fast=True 走快速模型（辩论/选举/会前流程）。
    """
    return _StructuredWrapper(schema, temperature, fast=fast)
