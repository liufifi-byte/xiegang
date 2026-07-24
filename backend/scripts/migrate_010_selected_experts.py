"""Add the user-confirmed expert roster to parliaments."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
ALTER TABLE parliaments
    ADD COLUMN IF NOT EXISTS selected_expert_ids JSONB;
"""


def main() -> None:
    db_url = re.sub(r"\?.*$", "", get_settings().supabase_db_url)
    with psycopg.connect(db_url, connect_timeout=60) as conn:
        with conn.cursor() as cursor:
            cursor.execute(SQL)
        conn.commit()
    print("Migration 010 complete: parliaments.selected_expert_ids")


if __name__ == "__main__":
    main()
