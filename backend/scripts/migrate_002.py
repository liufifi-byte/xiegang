"""升级 schema：5 议员颜色更新 + 新增 buy_bag / stocks 话题模板。

run with:
    cd /Users/xinyiliu/Desktop/ZCodeProject/council/backend
    ./.venv/bin/python scripts/migrate_002.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


# 新版议员颜色（现代杂志配色）
MEMBER_COLORS = {
    "suan": "#1E40AF",  # 老算 深蓝
    "chen": "#991B1B",  # 老沉 暗红
    "ji":   "#C2410C",  # 小机 橙
    "nian": "#15803D",  # 十年 深绿
    "fan":  "#6B21A8",  # 反对 深紫
}


# 新增的话题模板
NEW_TOPICS = [
    ("stocks", "买股票", "个股配置决策",
     '["ticker", "amount", "horizon", "thesis"]', "💱", 7),
    ("buy_bag", "买包", "奢侈品包决策（消费 / 投资 / 持有）",
     '["brand", "model", "price", "yearly_income", "hold_as_investment"]', "👜", 8),
]


def _clean_db_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


def main() -> None:
    settings = get_settings()
    db_url = _clean_db_url(settings.supabase_db_url)

    with psycopg.connect(db_url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            print("=== 升级议员颜色 ===")
            for mid, color in MEMBER_COLORS.items():
                cur.execute(
                    "UPDATE member_profiles SET color = %s WHERE id = %s",
                    (color, mid),
                )
                print(f"  ✓ {mid} → {color}")

            print("\n=== 新增话题模板 ===")
            for category, name, desc, vars_json, icon, sort in NEW_TOPICS:
                cur.execute(
                    """
                    INSERT INTO topic_templates
                        (category, display_name, description, key_variables, icon, sort_order)
                    VALUES
                        (%s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (category) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        description = EXCLUDED.description,
                        key_variables = EXCLUDED.key_variables,
                        icon = EXCLUDED.icon,
                        sort_order = EXCLUDED.sort_order
                    """,
                    (category, name, desc, vars_json, icon, sort),
                )
                print(f"  ✓ {category} ({name})")

            cur.execute(
                "SELECT category, display_name, icon FROM topic_templates ORDER BY sort_order"
            )
            print("\n=== topic_templates 当前 ===")
            for row in cur.fetchall():
                print(f"  {row[2]}  {row[1]}  ({row[0]})")

        conn.commit()

    print("\n✅ Migration 002 完成")


if __name__ == "__main__":
    main()
