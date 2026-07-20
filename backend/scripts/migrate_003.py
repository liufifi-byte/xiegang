"""升级 schema：5 议员颜色更新为「贤者厅」配色（低饱和度但有辨色度）。

run with:
    cd /Users/xinyiliu/Desktop/ZCodeProject/council/backend
    ./.venv/bin/python scripts/migrate_003.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


# 贤者厅配色：5 议员新色
MEMBER_COLORS = {
    "suan": "#3B5998",  # 老算 深青蓝
    "chen": "#7C2128",  # 老沉 骨瓷红
    "ji":   "#C78423",  # 小机 珠江黄
    "nian": "#2D5A3D",  # 十年 竺绿
    "fan":  "#5C3D6E",  # 反对 葡萄紫
}


def _clean_db_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


def main() -> None:
    settings = get_settings()
    db_url = _clean_db_url(settings.supabase_db_url)

    with psycopg.connect(db_url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            print("=== 升级议员颜色为「贤者厅」配色 ===")
            for mid, color in MEMBER_COLORS.items():
                cur.execute(
                    "UPDATE member_profiles SET color = %s WHERE id = %s",
                    (color, mid),
                )
                print(f"  ✓ {mid} → {color}")

            cur.execute(
                "SELECT id, display_name, color FROM member_profiles ORDER BY id"
            )
            print("\n=== member_profiles 当前 ===")
            for row in cur.fetchall():
                print(f"  {row[0]:6s} | {row[1]:4s} | {row[2]}")

        conn.commit()

    print("\n✅ Migration 003 完成")


if __name__ == "__main__":
    main()
