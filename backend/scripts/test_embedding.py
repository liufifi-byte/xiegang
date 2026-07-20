"""联调 GLM embedding API。

智谱开放平台 OpenAI 兼容 v4 endpoint，推测应该有 embedding-3 模型。
跑这个脚本验证：
    ./.venv/bin/python scripts/test_embedding.py

成功：打印 embeddings 维度
失败：换模型名重试，或降级到关键词匹配
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CANDIDATE_MODELS = [
    "embedding-3",
    "embedding-2",
    "text-embedding-3-small",
    "text-embedding-ada-002",
    "embedding",
]


async def try_one(model_name: str, settings) -> tuple[bool, str]:
    """试一个 embedding 模型。"""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=settings.glm_api_key,
            base_url=settings.glm_base_url,
        )
        resp = await client.embeddings.create(
            model=model_name,
            input="要不要买房",
        )
        vec = resp.data[0].embedding
        return True, f"模型={model_name} 维度={len(vec)} 前 3 值={vec[:3]}"
    except Exception as e:
        return False, f"模型={model_name} 失败: {type(e).__name__}: {str(e)[:140]}"


async def main():
    from app.config import get_settings
    settings = get_settings()

    print("=== 测试智谱 embedding API ===")
    print(f"base_url: {settings.glm_base_url}")
    print(f"key: {settings.glm_api_key[:12]}...")
    print()

    found = None
    for model in CANDIDATE_MODELS:
        ok, msg = await try_one(model, settings)
        print(f"  {'✓' if ok else '✗'} {msg}")
        if ok:
            found = model
            break

    print()
    if found:
        print(f"🎉 推荐用 embedding 模型：{found}")
        print(f"   写到 .env: GLM_EMBEDDING_MODEL={found}")
    else:
        print("⚠️ 所有候选 embedding 模型都失败。降级到关键词匹配 fallback。")
        print("   要继续验证：")
        print("   - 去智谱控制台确认 embedding 端点是否开放")
        print("   - 检查 API key 是否有 embedding 权限")
        print("   - 智谱 docs 查准确模型名")


if __name__ == "__main__":
    asyncio.run(main())
