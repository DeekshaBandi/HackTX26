"""
Turns call_events into a proper Tiger time-series setup. Safe to rerun.

What it sets up:
  - 6-hour chunks on the call_events hypertable
  - compression for chunks older than 2 hours
  - retention: raw call events delete themselves after 24 hours (privacy)
  - continuous aggregate calls_by_theme_hourly   (feeds the hold-or-go forecast)
  - continuous aggregate time_to_human_hourly    (how long the AI talks before handing off)

The aggregates refresh only the last 23 hours, which is shorter than the 24-hour
retention, so the hourly totals stay forever even after the raw rows are deleted.

Run:
    python tiger_analytics_setup.py
"""
from common import tiger_connect

RETENTION = "24 hours"

STEPS = [
    ("add metric column",
     "ALTER TABLE call_events ADD COLUMN IF NOT EXISTS value DOUBLE PRECISION"),

    ("6-hour chunks",
     "SELECT set_chunk_time_interval('call_events', INTERVAL '6 hours')"),

    ("compression settings",
     """ALTER TABLE call_events SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'event',
            timescaledb.compress_orderby = 'time DESC'
        )"""),

    ("compression policy (after 2 hours)",
     "SELECT add_compression_policy('call_events', INTERVAL '2 hours', if_not_exists => TRUE)"),

    (f"retention policy (delete raw events after {RETENTION})",
     f"SELECT add_retention_policy('call_events', INTERVAL '{RETENTION}', if_not_exists => TRUE)"),

    ("aggregate: calls per theme per hour",
     """CREATE MATERIALIZED VIEW IF NOT EXISTS calls_by_theme_hourly
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
               theme,
               count(*) AS calls
        FROM call_events
        WHERE event = 'call_ended' AND theme IS NOT NULL
        GROUP BY bucket, theme
        WITH NO DATA"""),

    ("refresh policy: calls_by_theme_hourly",
     """SELECT add_continuous_aggregate_policy('calls_by_theme_hourly',
            start_offset => INTERVAL '23 hours',
            end_offset => INTERVAL '1 minute',
            schedule_interval => INTERVAL '5 minutes',
            if_not_exists => TRUE)"""),

    ("column for Snowflake embeddings (768)",
     "ALTER TABLE students ADD COLUMN IF NOT EXISTS embedding_sf vector(768)"),

    ("vector index (HNSW, cosine) on Snowflake embeddings",
     """CREATE INDEX IF NOT EXISTS students_embedding_sf_hnsw
        ON students USING hnsw (embedding_sf vector_cosine_ops)"""),

    ("vector index (HNSW, cosine) on student embeddings",
     """CREATE INDEX IF NOT EXISTS students_embedding_hnsw
        ON students USING hnsw (embedding vector_cosine_ops)"""),

    ("index on matches (for the 'never matched before' check)",
     "CREATE INDEX IF NOT EXISTS matches_pair_idx ON matches (student_a, student_b)"),

    ("columns for the Muse Spark package (email, wants, ai_use, timing, summary, tags, crisis)",
     """ALTER TABLE students
            ADD COLUMN IF NOT EXISTS email   TEXT,
            ADD COLUMN IF NOT EXISTS wants   TEXT,
            ADD COLUMN IF NOT EXISTS ai_use  TEXT,
            ADD COLUMN IF NOT EXISTS timing  TEXT,
            ADD COLUMN IF NOT EXISTS summary TEXT,
            ADD COLUMN IF NOT EXISTS tags    TEXT,
            ADD COLUMN IF NOT EXISTS crisis  TEXT"""),

    ("chats table (in-app chat between matched students)",
     """CREATE TABLE IF NOT EXISTS chats (
            chat_id     TEXT PRIMARY KEY,
            match_id    TEXT,
            student_a   TEXT,
            student_b   TEXT,
            status      TEXT DEFAULT 'open',      -- 'open' -> 'ai_left'
            created_at  TIMESTAMPTZ DEFAULT now()
        )"""),

    ("chat_messages table",
     """CREATE TABLE IF NOT EXISTS chat_messages (
            id          BIGSERIAL PRIMARY KEY,
            chat_id     TEXT,
            sender_id   TEXT,                      -- a student_id, or 'aiedh' for the AI
            body        TEXT,
            created_at  TIMESTAMPTZ DEFAULT now()
        )"""),

    ("index on chat_messages",
     "CREATE INDEX IF NOT EXISTS chat_messages_chat_idx ON chat_messages (chat_id, id)"),

    ("toolkit extension (for percentiles)",
     "CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit"),
]

# Time-to-human aggregate: with percentiles if the toolkit is available, plain average if not.
TTH_WITH_PERCENTILES = """
    CREATE MATERIALIZED VIEW IF NOT EXISTS time_to_human_hourly
    WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
    SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
           count(*) AS handoffs,
           avg(value) AS avg_seconds,
           percentile_agg(value) AS pct
    FROM call_events
    WHERE event IN ('call_merge', 'group_text', 'in_app') AND value IS NOT NULL
    GROUP BY bucket
    WITH NO DATA"""

TTH_PLAIN = """
    CREATE MATERIALIZED VIEW IF NOT EXISTS time_to_human_hourly
    WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
    SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
           count(*) AS handoffs,
           avg(value) AS avg_seconds
    FROM call_events
    WHERE event IN ('call_merge', 'group_text', 'in_app') AND value IS NOT NULL
    GROUP BY bucket
    WITH NO DATA"""

TTH_POLICY = """
    SELECT add_continuous_aggregate_policy('time_to_human_hourly',
        start_offset => INTERVAL '23 hours',
        end_offset => INTERVAL '1 minute',
        schedule_interval => INTERVAL '5 minutes',
        if_not_exists => TRUE)"""


def run(conn, label, sql):
    try:
        conn.execute(sql)
        print(f"  ok       {label}")
        return True
    except Exception as e:
        first_line = str(e).strip().splitlines()[0]
        print(f"  skipped  {label}  ({first_line})")
        return False


conn = tiger_connect()
print("Setting up Tiger analytics...")
for label, sql in STEPS:
    run(conn, label, sql)

if not run(conn, "aggregate: time to human (with percentiles)", TTH_WITH_PERCENTILES):
    run(conn, "aggregate: time to human (average only)", TTH_PLAIN)
run(conn, "refresh policy: time_to_human_hourly", TTH_POLICY)

print("\nDone. 'skipped' is fine on reruns (it means that step already exists).")