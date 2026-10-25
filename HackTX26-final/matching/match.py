"""
Live matching. Person 2's code imports these functions:

    from match import start_call, update_profile, end_call, record_match

    sid = start_call(call_id="CA123", phone="+15125550100")
    result = update_profile(sid, {"major": "biology"}, call_id="CA123")
    result = update_profile(sid, {"situation": "left pre-med, haven't told parents"}, call_id="CA123")

    result["best"]        -> best match overall (fake or real), or None
    result["live_match"]  -> best match on a call RIGHT NOW who scores high enough, or None
    result["plan"]        -> {"decision": "merge_now" | "hold" | "group_text", "say": "...", ...}
    result["query_ms"]    -> how long the match search took

    record_match(sid, other_id, score, "call_merge", intro_text, call_id="CA123")  # once delivered
    end_call(sid, call_id="CA123")
"""
import time
import uuid
from collections import Counter

import numpy as np
from psycopg.types.json import Jsonb

from common import embed, profile_text, sf_embed, tiger_db, warm_up
from forecast import plan

# "Good enough" score differs per model, because each model spreads scores differently.
# From calibrate.py (95% of different-theme pairs score below these):
#   Snowflake arctic-embed: 0.83  (good matches seen: 0.86-0.92)
#   Local MiniLM: 0.35, but we keep 0.50 since good matches seen were 0.50-0.73
THRESHOLDS = {"embedding": 0.50, "embedding_sf": 0.83}
MATCH_THRESHOLD = THRESHOLDS["embedding"]  # kept for older imports

# Fields Person 2 is allowed to send (these match the Muse Spark "learned" package,
# except "themes" is stored in the column "tags" so it doesn't clash with our own "theme")
FIELDS = ["name", "school", "major", "year", "stage", "situation", "interests", "phone",
          "email", "wants", "ai_use", "timing", "summary", "tags"]


def _clean(v):
    """Lists become 'a, b, c'; blanks become None."""
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x).strip() for x in v if str(x).strip())
    v = str(v).strip() if v is not None else ""
    return v or None

RESULT_COLS = [
    "student_id", "name", "school", "major", "year", "situation", "stage",
    "interests", "theme", "online", "is_synthetic", "score",
]


def _db():
    return tiger_db()


# Last match we logged per call, so match_found is only logged when the best match changes
_last_logged = {}


def log_event(call_id, student_id, event, theme=None, detail=None, value=None):
    """Write one row to the call_events hypertable."""
    _db().execute(
        """
        INSERT INTO call_events (time, call_id, student_id, event, theme, detail, value)
        VALUES (now(), %s, %s, %s, %s, %s, %s)
        """,
        (call_id, student_id, event, theme, Jsonb(detail or {}), value),
    )


def start_call(call_id, phone=None, student_id=None):
    """Caller connected. Creates (or reuses) their student row and marks them online."""
    student_id = student_id or f"live_{uuid.uuid4().hex[:8]}"
    warm_up()  # load the model now, while the AI is still saying hello
    _db().execute(
        """
        INSERT INTO students (student_id, phone, is_synthetic, consented, online, last_seen)
        VALUES (%s, %s, FALSE, TRUE, TRUE, now())
        ON CONFLICT (student_id) DO UPDATE
            SET online = TRUE,
                last_seen = now(),
                phone = COALESCE(EXCLUDED.phone, students.phone)
        """,
        (student_id, phone),
    )
    log_event(call_id, student_id, "call_started")
    return student_id


# One round trip for both searches: top-k overall + best person on a call right now.
# The caller's vector comes from a scalar subquery so Postgres can use the HNSW index.
# col is "embedding_sf" (Snowflake) or "embedding" (local backup); whitelisted below.
def search_sql(col="embedding_sf"):
    assert col in THRESHOLDS
    me_vec = f"(SELECT {col} FROM students WHERE student_id = %(me)s)"
    return f"""
WITH base AS (
    SELECT s.student_id, s.name, s.school, s.major, s.year, s.situation, s.stage,
           s.interests, s.theme, s.online, s.is_synthetic, s.{col} AS v
    FROM students s
    WHERE s.student_id <> %(me)s
      AND s.{col} IS NOT NULL
      AND s.consented
      AND NOT s.safety_flag
      AND NOT EXISTS (
          SELECT 1 FROM matches m
          WHERE (m.student_a = %(me)s AND m.student_b = s.student_id)
             OR (m.student_b = %(me)s AND m.student_a = s.student_id)
      )
)
(SELECT student_id, name, school, major, year, situation, stage, interests, theme,
        online, is_synthetic, 1 - (v <=> {me_vec}) AS score, FALSE AS live_pick
 FROM base ORDER BY v <=> {me_vec} LIMIT %(k)s)
UNION ALL
(SELECT student_id, name, school, major, year, situation, stage, interests, theme,
        online, is_synthetic, 1 - (v <=> {me_vec}) AS score, TRUE AS live_pick
 FROM base WHERE online ORDER BY v <=> {me_vec} LIMIT 1)
"""


SEARCH_SQL = search_sql("embedding_sf")  # used by bench.py


def search(student_id, k=5, col="embedding_sf"):
    """Returns (top-k matches, best live match or None) in a single query."""
    rows = _db().execute(search_sql(col), {"me": student_id, "k": k}).fetchall()
    matches, live = [], None
    for r in rows:
        d = dict(zip(RESULT_COLS, r[:-1]))
        if r[-1]:
            live = d
        else:
            matches.append(d)
    return matches, live


def find_matches(student_id, k=5, online_only=False):
    """
    Best matches for this student, in one query:
    vector similarity + consented + no safety flag + never matched before (+ online now, if asked).
    """
    rows = _db().execute(
        """
        SELECT s.student_id, s.name, s.school, s.major, s.year, s.situation, s.stage,
               s.interests, s.theme, s.online, s.is_synthetic,
               1 - (s.embedding <=> me.embedding) AS score
        FROM students s
        JOIN students me ON me.student_id = %(me)s
        WHERE s.student_id <> me.student_id
          AND s.embedding IS NOT NULL
          AND s.consented
          AND NOT s.safety_flag
          AND (NOT %(online_only)s OR s.online)
          AND NOT EXISTS (
              SELECT 1 FROM matches m
              WHERE (m.student_a = me.student_id AND m.student_b = s.student_id)
                 OR (m.student_b = me.student_id AND m.student_a = s.student_id)
          )
        ORDER BY s.embedding <=> me.embedding
        LIMIT %(k)s
        """,
        {"me": student_id, "k": k, "online_only": online_only},
    ).fetchall()
    return [dict(zip(RESULT_COLS, r)) for r in rows]


def update_profile(student_id, fields, call_id=None):
    """
    Call this every time Person 2 learns something new about the caller.
    Re-embeds only when we know their situation AND it actually changed.
    """
    fields = {k: _clean(v) for k, v in fields.items() if k in FIELDS}
    fields = {k: v for k, v in fields.items() if v is not None}  # blanks never overwrite earlier answers

    cols = ["name", "school", "major", "year", "stage", "situation", "interests",
            "timing", "summary", "profile_text", "safety_flag", "consented"]
    row = _db().execute(
        f"SELECT {', '.join(cols)}, embedding_sf IS NOT NULL FROM students WHERE student_id = %s",
        (student_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No student {student_id}. Call start_call() first.")
    current = dict(zip(cols, row))
    has_sf = bool(row[-1])
    current.update(fields)

    new_text = profile_text(current)
    has_situation = bool(new_text)
    reembed = has_situation and new_text != row[cols.index("profile_text")]

    # Field names come from the FIELDS whitelist, so building the SET clause is safe.
    sets = [f"{k} = %s" for k in fields] + ["updated_at = now()", "last_seen = now()"]
    params = list(fields.values())
    embed_ms = None
    if reembed:
        t0 = time.perf_counter()
        sf_vec = None
        try:
            sf_vec = np.array(sf_embed([new_text])[0])   # Snowflake Cortex (main)
        except Exception as e:
            print(f"Snowflake embed failed, using local backup: {str(e).splitlines()[0]}")
        embed_ms = round((time.perf_counter() - t0) * 1000, 1)
        has_sf = sf_vec is not None
        sets += ["profile_text = %s", "embedding = %s", "embedding_sf = %s"]
        params += [new_text, np.array(embed([new_text])[0]), sf_vec]
    _db().execute(f"UPDATE students SET {', '.join(sets)} WHERE student_id = %s", params + [student_id])

    col = "embedding_sf" if has_sf else "embedding"
    log_event(call_id, student_id, "profile_updated",
              detail={"fields": list(fields), "reembedded": reembed,
                      "embedder": ("snowflake" if has_sf else "local") if reembed else None},
              value=embed_ms)

    result = {"student_id": student_id, "embedded": reembed, "matches": [], "best": None,
              "live_match": None, "plan": None, "query_ms": None,
              "embedder": "snowflake" if col == "embedding_sf" else "local", "embed_ms": embed_ms}
    if not has_situation or current["safety_flag"] or not current["consented"]:
        return result  # not enough info yet, or not eligible to match

    t0 = time.perf_counter()
    matches, live = search(student_id, col=col)
    result["query_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    result["matches"] = matches
    result["best"] = matches[0] if matches else None
    threshold = THRESHOLDS[col]
    if live and live["score"] >= threshold:
        result["live_match"] = live

    # Give live callers a theme (most common among their closest fake students),
    # so they get a cluster on the judge site and count in Tiger's aggregates.
    themes = [m["theme"] for m in matches if m["is_synthetic"] and m["theme"]]
    theme = Counter(themes).most_common(1)[0][0] if themes else None
    if theme:
        _db().execute("UPDATE students SET theme = %s WHERE student_id = %s", (theme, student_id))

    result["plan"] = plan(theme, has_live_match=result["live_match"] is not None)
    # Caller said they'd rather talk later: don't hold them on the line
    timing = (current.get("timing") or "").lower()
    if timing == "later" and result["plan"]["decision"] == "hold":
        result["plan"] = plan(None, has_live_match=False)
        result["plan"]["theme"] = theme

    best = result["best"]
    key = (call_id, student_id)
    if best and best["score"] >= threshold and _last_logged.get(key) != best["student_id"]:
        _last_logged[key] = best["student_id"]
        log_event(call_id, student_id, "match_found", theme=theme, value=result["query_ms"],
                  detail={"match": best["student_id"], "score": round(best["score"], 3),
                          "live": result["live_match"] is not None,
                          "decision": result["plan"]["decision"]})
    return result


def record_match(student_a, student_b, score, method, intro_text=None, call_id=None):
    """
    Save a match once it's delivered (method: 'call_merge', 'group_text', or 'in_app').
    Also records how long the AI talked before handing off (time to human).
    """
    match_id = f"m_{uuid.uuid4().hex[:10]}"
    _db().execute(
        """
        INSERT INTO matches (match_id, student_a, student_b, score, method, intro_text)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (match_id, student_a, student_b, score, method, intro_text),
    )
    seconds = _db().execute(
        """
        SELECT EXTRACT(epoch FROM now() - min(time)) FROM call_events
        WHERE call_id = %s AND event = 'call_started'
        """,
        (call_id,),
    ).fetchone()[0]
    theme = _db().execute("SELECT theme FROM students WHERE student_id = %s", (student_a,)).fetchone()
    log_event(call_id, student_a, method, theme=theme[0] if theme else None,
              detail={"match_id": match_id, "with": student_b},
              value=float(seconds) if seconds is not None else None)
    return match_id


def end_call(student_id, call_id=None):
    """Caller hung up."""
    row = _db().execute(
        "UPDATE students SET online = FALSE, last_seen = now() WHERE student_id = %s RETURNING theme",
        (student_id,),
    ).fetchone()
    log_event(call_id, student_id, "call_ended", theme=row[0] if row else None)


def apply_package(student_id, package, call_id=None):
    """
    Takes Muse Spark's per-turn package exactly as Person 2 gets it:
        {"say": ..., "ready_for_next_phase": ..., "learned": {...},
         "confidence": {...}, "summary": "...", "crisis": "none" | "concern" | "urgent"}
    Saves what's new, handles the crisis check, re-embeds if the situation changed,
    and returns the same result as update_profile (matches, plan, ...).
    """
    learned = dict(package.get("learned") or package.get("profile") or {})
    if "themes" in learned:
        learned["tags"] = learned.pop("themes")
    if package.get("summary"):
        learned["summary"] = package["summary"]

    crisis = (package.get("crisis") or "none")
    crisis_level = crisis if isinstance(crisis, str) else crisis.get("level", "none")
    if crisis_level in ("concern", "urgent"):
        # Never match someone who might be in crisis with a peer. The call goes to the crisis path.
        _db().execute("UPDATE students SET safety_flag = TRUE, crisis = %s WHERE student_id = %s",
                      (crisis_level, student_id))
        log_event(call_id, student_id, "crisis", detail={"level": crisis_level})

    result = update_profile(student_id, learned, call_id=call_id)
    result["crisis"] = crisis_level
    result["confidence"] = package.get("confidence") or {}
    return result


def ingest_call(record):
    """
    Takes the end-of-call record Person 1/2's code produces
    ({"call_id", "caller_phone", "profile", "summary", "crisis", "confidence", "transcript", ...}),
    saves the caller to the waiting pool, and finds their best match for a later group text / in-app intro.
    The transcript is NOT stored (privacy: Tiger only keeps profile fields and call events).
    Repeat callers keep the same student_id, based on their phone number.
    """
    phone = record.get("caller_phone")
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    sid = f"ph_{digits[-10:]}" if digits else None
    call_id = record.get("call_id")

    sid = start_call(call_id, phone=phone, student_id=sid)
    result = apply_package(sid, {
        "profile": record.get("profile") or {},
        "summary": record.get("summary"),
        "crisis": record.get("crisis"),
        "confidence": record.get("confidence"),
    }, call_id=call_id)
    end_call(sid, call_id=call_id)

    # The call is over, so nobody can be merged live: it's an async connection
    if result.get("plan"):
        result["plan"] = plan(result["plan"].get("theme"), has_live_match=False)
        result["plan"]["decision"] = "group_text"
    result["student_id"] = sid

    # Good match? Connect them in the app right away. Otherwise they stay in the waiting pool.
    result["delivered"] = None
    best = result.get("best")
    col = "embedding_sf" if result.get("embedder") == "snowflake" else "embedding"
    # Merged into a live call with someone: that match is already recorded (deliver, method call_merge)
    if record.get("merged_with"):
        best = None
    if result.get("crisis") in (None, "none") and best and best["score"] >= THRESHOLDS[col]:
        from deliver import deliver  # imported here to avoid a circular import
        result["delivered"] = deliver(sid, best["student_id"], best["score"], call_id=call_id, method="in_app")
    return result