"""
Embeds every student in students.json (Snowflake Cortex + local backup) and loads them into Tiger.
Replaces any fake students already there, so it's safe to rerun.

Run (after tiger_analytics_setup.py, which adds the embedding_sf column):
    python load_tiger.py
"""
import json

import numpy as np

from common import embed, embed_both, profile_text, sf_embed, tiger_connect

students = json.load(open("students.json"))
texts = [profile_text(s) for s in students]

print(f"Embedding {len(students)} students (local + Snowflake Cortex)...")
local_vecs, sf_vecs = embed_both(texts)
if sf_vecs is None:
    raise SystemExit("Snowflake embedding failed, so nothing was loaded. Check the error above.")
print(f"  local: {len(local_vecs[0])} numbers each, Snowflake: {len(sf_vecs[0])} numbers each")

rows = [
    (
        s["student_id"], s["name"], s["school"], s["major"], s["year"],
        s["stage"], s["situation"], s["interests"], s["theme"],
        text, np.array(lv), np.array(sv),
    )
    for s, text, lv, sv in zip(students, texts, local_vecs, sf_vecs)
]

conn = tiger_connect()
print("Uploading to Tiger...")
conn.execute("DELETE FROM students WHERE is_synthetic")
with conn.cursor() as cur:
    cur.executemany(
        """
        INSERT INTO students (
            student_id, name, school, major, year, stage, situation, interests, theme,
            profile_text, embedding, embedding_sf, is_synthetic, consented, online
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE, TRUE, FALSE)
        """,
        rows,
    )

count = conn.execute("SELECT COUNT(*) FROM students WHERE is_synthetic").fetchone()[0]
print(f"Loaded {count} fake students.\n")

# Side-by-side: same caller, both models. Pick whichever looks better.
caller = "thinking about leaving pre-med but scared to tell my parents"
for label, col, qvec in [
    ("Snowflake Cortex", "embedding_sf", np.array(sf_embed([caller])[0])),
    ("Local MiniLM", "embedding", np.array(embed([caller])[0])),
]:
    results = conn.execute(
        f"""
        SELECT name, school, situation, 1 - ({col} <=> %s) AS score
        FROM students
        WHERE consented AND NOT safety_flag AND {col} IS NOT NULL
        ORDER BY {col} <=> %s
        LIMIT 5
        """,
        (qvec, qvec),
    ).fetchall()
    print(f"{label}, caller: {caller}")
    for name, school, situation, score in results:
        print(f"  {score:.3f}  {name} ({school}): {situation}")
    print()

conn.close()