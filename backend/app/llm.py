"""GLM-5 (智谱) LLM 客户端。

通过 langchain_openai.ChatOpenAI + OpenAI 兼容协议接入智谱。

关键工作：get_structured_llm 包了一层容错 wrapper——
GLM-5 通过 OpenAI 兼容协议时，with_structured_output 偶尔会因为模型输出
```json ... ``` markdown fence 而失败。本 wrapper 在原生 structured 失败时
降级到普通 invoke + 手动 JSON 提取 + 重新校验。
"""
import json
import re
from typing import TypeVar

from pydantic import BaseModel

from app.config import get_settings


T = TypeVar("T", bound=BaseModel)


def get_llm(temperature: float = 0.7, max_tokens: int = 2000):
    """返回配置好的 GLM-5 ChatOpenAI 客户端。"""
    # 延迟 import：避免在 module import 时就创建客户端（测试环境用不上）
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    return ChatOpenAI(
        model=settings.glm_model,
        openai_api_key=settings.glm_api_key,
        openai_api_base=settings.glm_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=settings.glm_request_timeout_seconds,
        max_retries=settings.glm_max_retries,
    )


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
      1. 先试原生 with_structured_output (走 function calling)
      2. 原生失败 → 用普通 invoke 调一次 + 手动剥 fence + json.loads + pydantic 校验
      3. 仍失败 → 抛原始异常（让上层 catch 走兜底）
    """

    def __init__(self, schema: type[T], temperature: float):
        self.schema = schema
        self.llm = get_llm(temperature=temperature)
        self.structured = self.llm.with_structured_output(schema)

    def invoke(self, prompt, **kwargs):
        try:
            return self.structured.invoke(prompt, **kwargs)
        except Exception as structured_err:
            # 降级：手动调用 + parse
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
        失败时降级到 llm.ainvoke + 手动 JSON 解析。
        """
        try:
            return await self.structured.ainvoke(prompt, **kwargs)
        except Exception as structured_err:
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


def get_structured_llm(schema: type[BaseModel], temperature: float = 0.5):
    """返回容错的结构化输出 LLM。

    返回对象支持 `.invoke(prompt)` 同步 和 `.ainvoke(prompt)` 异步接口，
    返回 Pydantic schema 实例。
    """
    return _StructuredWrapper(schema, temperature)
