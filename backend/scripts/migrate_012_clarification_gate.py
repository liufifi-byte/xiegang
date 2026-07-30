"""Add durable clarification state for the pre-research information gate."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
alter table parliaments
  add column if not exists clarification_status text not null default 'not_checked',
  add column if not exists clarification_questions jsonb not null default '[]'::jsonb,
  add column if not exists clarification_answers jsonb not null default '[]'::jsonb,
  add column if not exists clarification_requested_at timestamptz,
  add column if not exists clarification_answered_at timestamptz;

create index if not exists idx_parliaments_clarification_status
  on parliaments(clarification_status);
"""


def main() -> None:
    db_url = re.sub(r"\?.*$", "", get_settings().supabase_db_url)
    with psycopg.connect(db_url, connect_timeout=60) as conn:
        with conn.cursor() as cursor:
            cursor.execute(SQL)
        conn.commit()
    print("migration 012 complete")


if __name__ == "__main__":
    main()
