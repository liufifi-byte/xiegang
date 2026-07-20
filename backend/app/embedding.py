"""GLM-5 (智谱) Embedding 客户端。

V2-S3.1 验证：embedding-3 模型在智谱 v4 endpoint 真可用，2048 维。
V2-S3 限速：ivfflat 索引限 2000 维，所以截取前 2000 维（线性丢弃最后 48 维）。
HNSW 启用后可以放弃这个限制（migrate_005_fix 已用 fallback 路线）。
"""
from functools import lru_cache

from app.config import get_settings

# ivfflat 限制 → 截 2000 维
EMBEDDING_DIM = 2000


@lru_cache
def _get_openai_async():
    """惰性创建 AsyncOpenAI 客户端单例（绑定智谱 base_url）。"""
    from openai import AsyncOpenAI

    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
    )


def _trim(vec: list[float]) -> list[float]:
    """截到 2000 维。"""
    return vec[:EMBEDDING_DIM]


async def embed_query(text: str) -> list[float]:
    """把一段文本转为 2000 维向量。"""
    client = _get_openai_async()
    settings = get_settings()
    resp = await client.embeddings.create(
        model=settings.glm_embedding_model,
        input=text,
    )
    return _trim(resp.data[0].embedding)


def embed_query_sync(text: str) -> list[float]:
    """同步版本（archive 节点用）。"""
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
    )
    resp = client.embeddings.create(
        model=settings.glm_embedding_model,
        input=text,
    )
    return _trim(resp.data[0].embedding)
