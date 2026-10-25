"""
Finds a fair "good enough match" score for each embedding model.

It samples pairs of fake students from the SAME theme (should match)
and from DIFFERENT themes (shouldn't), then suggests the cutoff that
keeps 95% of different-theme pairs out.

Run:
    python calibrate.py
Then put the suggested numbers into THRESHOLDS at the top of match.py.
"""
from common import tiger_connect

PAIRS = 3000

conn = tiger_connect()

for col, label in [("embedding_sf", "Snowflake Cortex"), ("embedding", "Local MiniLM")]:
    same, diff = conn.execute(
        f"""
        WITH s AS (
            SELECT student_id, theme, {col} AS v FROM students
            WHERE is_synthetic AND {col} IS NOT NULL
        ),
        pairs AS (
            SELECT a.theme = b.theme AS same_theme, 1 - (a.v <=> b.v) AS score
            FROM s a JOIN s b ON a.student_id < b.student_id
            ORDER BY random()
            LIMIT %s * 30
        ),
        same AS (SELECT score FROM pairs WHERE same_theme LIMIT %s),
        diff AS (SELECT score FROM pairs WHERE NOT same_theme LIMIT %s)
        SELECT
          (SELECT json_build_object(
              'median', percentile_cont(0.5) WITHIN GROUP (ORDER BY score),
              'p25',    percentile_cont(0.25) WITHIN GROUP (ORDER BY score)) FROM same),
          (SELECT json_build_object(
              'median', percentile_cont(0.5) WITHIN GROUP (ORDER BY score),
              'p95',    percentile_cont(0.95) WITHIN GROUP (ORDER BY score)) FROM diff)
        """,
        (PAIRS, PAIRS, PAIRS),
    ).fetchone()

    print(f"{label}:")
    print(f"   same theme:       median {same['median']:.3f}   (25% of them below {same['p25']:.3f})")
    print(f"   different themes: median {diff['median']:.3f}   (95% of them below {diff['p95']:.3f})")
    print(f"   suggested cutoff: {diff['p95']:.2f}\n")