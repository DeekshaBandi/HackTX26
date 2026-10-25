"""
Creates the Tiger Data tables. Safe to rerun.

Setup (once):
    python -m pip install "psycopg[binary]" pgvector
    .env needs:  TIGER_URL=postgres://...

Run:
    python tiger_setup.py
"""
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

SQL = """
-- pgvector: lets Postgres store embeddings and search by similarity
CREATE EXTENSION IF NOT EXISTS vector;

-- Every student, fake or real. Live callers get a row that fills in during the call.
CREATE TABLE IF NOT EXISTS students (
    student_id    TEXT PRIMARY KEY,
    name          TEXT,
    school        TEXT,
    major         TEXT,
    year          TEXT,
    stage         TEXT,
    situation     TEXT,
    interests     TEXT,
    theme         TEXT,
    profile_text  TEXT,
    embedding     vector(384),
    is_synthetic  BOOLEAN DEFAULT TRUE,
    consented     BOOLEAN DEFAULT TRUE,
    online        BOOLEAN DEFAULT FALSE,   -- on a call right now
    safety_flag   BOOLEAN DEFAULT FALSE,   -- set by the safety check; never matched while true
    phone         TEXT,
    last_seen     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

-- Every match we make and how it was delivered
CREATE TABLE IF NOT EXISTS matches (
    match_id    TEXT PRIMARY KEY,
    student_a   TEXT,
    student_b   TEXT,
    score       DOUBLE PRECISION,
    method      TEXT,          -- 'call_merge', 'group_text', or 'in_app'
    intro_text  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Time-series log of what happens on each call (stage changes, not transcripts).
-- This is the hypertable: Tiger's core feature. Feeds the hold-or-go decision and dashboards.
CREATE TABLE IF NOT EXISTS call_events (
    time        TIMESTAMPTZ NOT NULL,
    call_id     TEXT,
    student_id  TEXT,
    event       TEXT,          -- call_started, profile_updated, match_found, held, merged, group_text, call_ended
    theme       TEXT,
    detail      JSONB
);
SELECT create_hypertable('call_events', by_range('time'), if_not_exists => TRUE);
"""

with psycopg.connect(os.environ["TIGER_URL"], autocommit=True) as conn:
    conn.execute(SQL)
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1"
    ).fetchall()
    print("Tiger is set up. Tables:", ", ".join(t[0] for t in tables))