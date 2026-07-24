"""Add durable live-council phase, client lease, and worker heartbeat state."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
alter table parliaments
  add column if not exists current_phase text not null default 'queued',
  add column if not exists last_client_heartbeat timestamptz,
  add column if not exists keep_alive_until timestamptz;

create table if not exists worker_heartbeats (
  worker_id text primary key,
  last_seen_at timestamptz not null default now(),
  capacity integer not null default 1,
  active_count integer not null default 0
);

create index if not exists idx_worker_heartbeats_last_seen
  on worker_heartbeats(last_seen_at desc);
"""


def main() -> None:
    db_url = re.sub(r"\?.*$", "", get_settings().supabase_db_url)
    with psycopg.connect(db_url, connect_timeout=60) as conn:
        with conn.cursor() as cursor:
            cursor.execute(SQL)
        conn.commit()
    print("migration 011 complete")


if __name__ == "__main__":
    main()
