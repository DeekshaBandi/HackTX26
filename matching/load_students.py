"""
Embeds every student in students.json and loads them into Snowflake.
Replaces any fake students already there, so it's safe to rerun.

Run:
    python load_students.py
"""
import json

from common import DIM, connect, embed, profile_text

CHUNK = 50  # rows per insert, keeps each SQL statement small

students = json.load(open("students.json"))
print(f"Embedding {len(students)} students...")
texts = [profile_text(s) for s in students]
vectors = embed(texts)

conn = connect()
cur = conn.cursor()

# Staging table holds the embedding as text, then we convert it to a VECTOR in one go.
cur.execute("""
    CREATE OR REPLACE TEMPORARY TABLE STUDENTS_STAGE (
        STUDENT_ID STRING, NAME STRING, SCHOOL STRING, MAJOR STRING, YEAR STRING,
        STAGE STRING, SITUATION STRING, INTERESTS STRING, THEME STRING,
        PROFILE_TEXT STRING, EMB STRING
    )
""")

rows = [
    (
        s["student_id"], s["name"], s["school"], s["major"], s["year"],
        s["stage"], s["situation"], s["interests"], s["theme"],
        text, json.dumps(vec),
    )
    for s, text, vec in zip(students, texts, vectors)
]

print("Uploading to Snowflake...")
for i in range(0, len(rows), CHUNK):
    cur.executemany(
        "INSERT INTO STUDENTS_STAGE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rows[i:i + CHUNK],
    )
    print(f"  {min(i + CHUNK, len(rows))}/{len(rows)}")

cur.execute("DELETE FROM STUDENTS WHERE IS_SYNTHETIC")
cur.execute(f"""
    INSERT INTO STUDENTS (
        STUDENT_ID, NAME, SCHOOL, MAJOR, YEAR, STAGE, SITUATION, INTERESTS, THEME,
        PROFILE_TEXT, EMBEDDING, IS_SYNTHETIC, CONSENTED, ONLINE
    )
    SELECT
        STUDENT_ID, NAME, SCHOOL, MAJOR, YEAR, STAGE, SITUATION, INTERESTS, THEME,
        PROFILE_TEXT, PARSE_JSON(EMB)::VECTOR(FLOAT, {DIM}), TRUE, TRUE, FALSE
    FROM STUDENTS_STAGE
""")

cur.execute("SELECT COUNT(*) FROM STUDENTS WHERE IS_SYNTHETIC")
print(f"Loaded {cur.fetchone()[0]} fake students.\n")

# Quick sanity check: who would a sample caller match with?
caller = {
    "year": "sophomore",
    "major": "biology",
    "situation": "thinking about leaving pre-med but scared to tell my parents",
    "stage": "just started thinking about it",
}
cur.execute(f"""
    SELECT NAME, SCHOOL, SITUATION, STAGE,
           VECTOR_COSINE_SIMILARITY(EMBEDDING, PARSE_JSON(%s)::VECTOR(FLOAT, {DIM})) AS SCORE
    FROM STUDENTS
    ORDER BY SCORE DESC
    LIMIT 5
""", (json.dumps(embed([profile_text(caller)])[0]),))

print(f"Sample caller: {caller['situation']}\nTop 5 matches:")
for name, school, situation, stage, score in cur.fetchall():
    print(f"  {score:.3f}  {name} ({school}): {situation} [{stage}]")

cur.close()
conn.close()