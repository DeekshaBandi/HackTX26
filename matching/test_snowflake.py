"""
End-to-end check: embed text in Python, store it in Snowflake, search in Snowflake.

Setup (once):
    pip install snowflake-connector-python python-dotenv sentence-transformers

.env file in the same folder:
    SNOWFLAKE_ACCOUNT=yourorg-youraccount
    SNOWFLAKE_USER=AIEDH_APP
    SNOWFLAKE_KEY_FILE=rsa_key.p8

Run:
    python test_snowflake.py
"""
import json
import os

import snowflake.connector
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()

# First run downloads the model (~90 MB). After that it's cached.
model = SentenceTransformer("all-MiniLM-L6-v2")

def embed(text: str) -> str:
    """Returns the embedding as a JSON list string, ready to send to Snowflake."""
    vec = model.encode(text, normalize_embeddings=True)
    return json.dumps(vec.tolist())

conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_USER"],
    private_key_file=os.environ["SNOWFLAKE_KEY_FILE"],
    role="AIEDH_APP_ROLE",
    warehouse="AIEDH_WH",
    database="AIEDH",
    schema="MATCHING",
)
cur = conn.cursor()

# Three test students
test_students = [
    ("test_1", "dropped orgo and switching majors, scared to tell my mom and dad"),
    ("test_2", "I moved to a new city and do not know anyone"),
    ("test_3", "failing my classes and thinking about leaving college"),
]

for sid, text in test_students:
    cur.execute(
        """
        INSERT INTO STUDENTS (STUDENT_ID, PROFILE_TEXT, EMBEDDING)
        SELECT %s, %s, PARSE_JSON(%s)::VECTOR(FLOAT, 384)
        """,
        (sid, text, embed(text)),
    )

# The "caller"
caller = "I left pre-med and have not told my parents yet"
cur.execute(
    """
    SELECT STUDENT_ID, PROFILE_TEXT,
           VECTOR_COSINE_SIMILARITY(EMBEDDING, PARSE_JSON(%s)::VECTOR(FLOAT, 384)) AS SCORE
    FROM STUDENTS
    WHERE STUDENT_ID LIKE 'test_%%'
    ORDER BY SCORE DESC
    """,
    (embed(caller),),
)

print(f"Caller: {caller}\n")
for sid, text, score in cur.fetchall():
    print(f"{score:.3f}  {text}")

# Clean up test rows
cur.execute("DELETE FROM STUDENTS WHERE STUDENT_ID LIKE 'test_%'")
cur.close()
conn.close()