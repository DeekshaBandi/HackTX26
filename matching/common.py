"""
Shared helpers for the matching code. Everything else imports from here,
so fake students and live callers are embedded the exact same way.

Two embedders:
  - Snowflake Cortex AI_EMBED (main one, 768 numbers)  -> Tiger column embedding_sf
  - local all-MiniLM-L6-v2 (backup, 384 numbers)       -> Tiger column embedding
Every student gets both, so matching keeps working if Snowflake is unreachable.
"""
import json
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

EMBED_MODEL = "all-MiniLM-L6-v2"
DIM = 384
SF_EMBED_MODEL = os.environ.get("SF_EMBED_MODEL", "snowflake-arctic-embed-m-v1.5")
SF_DIM = 768


# ---------- local embedder (backup) ----------

@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    try:
        # Use the copy already downloaded, so we don't need the internet (or huggingface.co) to embed
        return SentenceTransformer(EMBED_MODEL, local_files_only=True)
    except Exception:
        return SentenceTransformer(EMBED_MODEL)  # first run on a new machine: download it


def embed(texts):
    """Local embedder: list of strings -> list of 384-number lists."""
    vecs = _model().encode(
        texts,
        normalize_embeddings=True,
        batch_size=64,
        show_progress_bar=len(texts) > 50,
    )
    return [v.tolist() for v in vecs]


# ---------- Snowflake Cortex embedder (main) ----------

@lru_cache(maxsize=1)
def _sf():
    return connect()


def sf_embed(texts, chunk=200):
    """
    Snowflake Cortex AI_EMBED: list of strings -> list of 768-number lists.
    Sends texts as one JSON array per chunk, so it's one round trip per 200 texts.
    """
    out = []
    cur = _sf().cursor()
    for i in range(0, len(texts), chunk):
        part = texts[i:i + chunk]
        cur.execute(
            f"""
            SELECT f.index, AI_EMBED('{SF_EMBED_MODEL}', f.value::STRING)::ARRAY
            FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) f
            ORDER BY f.index
            """,
            (json.dumps(part),),
        )
        for _, arr in cur.fetchall():
            out.append(json.loads(arr) if isinstance(arr, str) else list(arr))
    cur.close()
    return out


def embed_both(texts):
    """
    Returns (local_vectors, snowflake_vectors_or_None).
    If Snowflake fails, the second value is None and callers fall back to local.
    """
    local = embed(texts)
    try:
        sf = sf_embed(texts)
    except Exception as e:
        print(f"Snowflake embedding failed, using local only: {str(e).splitlines()[0]}")
        sf = None
    return local, sf


def warm_up():
    """Load the local model and wake the Snowflake warehouse, so the first real embed is fast."""
    _model().encode(["warm up"])
    try:
        _sf().cursor().execute("SELECT 1")
    except Exception:
        pass


# ---------- matching text ----------

def profile_text(p):
    """
    The text we embed and match on: ONLY what the student is going through.
    Major, year, and interests are saved but left out on purpose. In testing,
    a hobby like "cooking" pulled a lonely student toward a homesick-for-food match.
    Those fields are for the intro ("you both play basketball"), not for matching.
    """
    situation = (p.get("situation") or "").strip()
    summary = (p.get("summary") or "").strip()
    # Real calls sometimes give a very thin situation ("Feeling lonely"). In that case
    # add the first sentence of the AI's summary, which usually has the real story.
    if situation and summary and len(situation.split()) < 6:
        first = summary.split(". ")[0].rstrip(".")
        return f"{situation}. {first}."
    return situation


# ---------- connections ----------

def tiger_connect():
    """Connection to Tiger Data (Postgres + TimescaleDB + pgvector)."""
    import time

    import psycopg
    from pgvector.psycopg import register_vector

    for attempt in range(1, 4):
        try:
            conn = psycopg.connect(
                os.environ["TIGER_URL"],
                autocommit=True,
                connect_timeout=10,   # give up after 10s instead of hanging
                keepalives=1,         # keep the connection alive on flaky networks
                keepalives_idle=30,
            )
            register_vector(conn)
            return conn
        except psycopg.OperationalError:
            if attempt == 3:
                raise SystemExit(
                    "Can't reach Tiger after 3 tries. Your network is probably blocking port "
                    "36626 (common on venue Wi-Fi). Try a phone hotspot."
                )
            print(f"Tiger connection failed (try {attempt}/3), retrying...")
            time.sleep(2)


def connect():
    """Connection to Snowflake."""
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key_file=os.environ["SNOWFLAKE_KEY_FILE"],
        role="AIEDH_APP_ROLE",
        warehouse="AIEDH_WH",
        database="AIEDH",
        schema="MATCHING",
        login_timeout=15,
        network_timeout=30,
    )