"""Seed 脚本：把 5 个议员档案写入 Supabase member_profiles 表。

用法（在 backend 目录下）：
    python scripts/seed_members.py

文案 + 工具集 + few-shot 范例全部从 app/prompts/*.md 加载。
"""
import re
import sys
from pathlib import Path

# 让脚本能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


MEMBERS = [
    {
        "id": "suan",
        "display_name": "老算",
        "color": "#2B4F8C",  # 奇葩说：深青蓝
        "prompt_file": "suan.md",
        "tools": ["web_search", "read_url", "user_memory_query", "calculator", "data_table"],
    },
    {
        "id": "chen",
        "display_name": "老沉",
        "color": "#DC3F4B",  # 奇葩说：番茄红（正方色）
        "prompt_file": "chen.md",
        "tools": ["web_search", "read_url", "user_memory_query", "historical_risk_lookup"],
    },
    {
        "id": "ji",
        "display_name": "小机",
        "color": "#E8B441",  # 奇葩说：暖金（金句色）
        "prompt_file": "ji.md",
        "tools": ["web_search", "read_url", "user_memory_query", "market_window_check"],
    },
    {
        "id": "nian",
        "display_name": "十年",
        "color": "#1E7C5B",  # 奇葩说：竺绿
        "prompt_file": "nian.md",
        "tools": ["web_search", "read_url", "user_memory_query", "trend_research"],
    },
    {
        "id": "fan",
        "display_name": "反对",
        "color": "#5C3D6E",  # 葡萄紫（保留）
        "prompt_file": "fan.md",
        "tools": ["web_search", "read_url", "user_memory_query", "contrarian_search"],
    },
]

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "app" / "prompts"


def _clean_db_url(url: str) -> str:
    """去掉 ?pgbouncer=true 等查询字符串（psycopg 不喜欢某些参数）。"""
    return re.sub(r"\?.*$", "", url)


def main() -> None:
    settings = get_settings()
    db_url = _clean_db_url(settings.supabase_db_url)

    with psycopg.connect(db_url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            for m in MEMBERS:
                prompt_path = PROMPTS_DIR / m["prompt_file"]
                system_prompt = prompt_path.read_text(encoding="utf-8")

                # few-shot：从 prompt 第 6 层往后切出来
                if "## 第 6 层：Few-shot" in system_prompt:
                    few_shot_raw = system_prompt.split("## 第 6 层：Few-shot", 1)[1].strip()
                else:
                    few_shot_raw = ""
                few_shot_examples = [{"raw": few_shot_raw}] if few_shot_raw else []

                cur.execute(
                    """
                    INSERT INTO member_profiles
                        (id, display_name, color, system_prompt, tools, few_shot_examples, version, is_active)
                    VALUES
                        (%s, %s, %s, %s, %s::jsonb, %s::jsonb, 1, true)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        color = EXCLUDED.color,
                        system_prompt = EXCLUDED.system_prompt,
                        tools = EXCLUDED.tools,
                        few_shot_examples = EXCLUDED.few_shot_examples,
                        version = member_profiles.version + 1,
                        updated_at = now()
                    """,
                    (
                        m["id"],
                        m["display_name"],
                        m["color"],
                        system_prompt,
                        # psycopg 要求 jsonb 用 Json 包装或直接传字符串
                        __import__("json").dumps(m["tools"]),
                        __import__("json").dumps(few_shot_examples, ensure_ascii=False),
                    ),
                )
                print(f"✓ Seeded {m['id']} ({m['display_name']})")

            cur.execute(
                "SELECT id, display_name, color, version FROM member_profiles ORDER BY id"
            )
            print("\n--- member_profiles 当前状态 ---")
            for row in cur.fetchall():
                print(f"  {row[0]:6s} | {row[1]:4s} | {row[2]}  | v{row[3]}")

        conn.commit()

    print("\n所有议员档案已写入 member_profiles 表。")


if __name__ == "__main__":
    main()
