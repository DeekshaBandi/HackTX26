"""
Small web server so the other parts of AIEDH can use matching over HTTP:
  - Person 2 (AI operator): start / update / end calls, deliver matches
  - Nathan (judge site):    students with 3D positions, live events, metrics
  - Person 4 (student site): a student's profile, matches, and chats

Setup (once):
    python -m pip install fastapi uvicorn scikit-learn twilio

Run:
    uvicorn api:app --host 0.0.0.0 --port 8000

Then open http://localhost:8000/docs to see and try every endpoint.
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import deliver as delivery
import match
import metrics as tiger_metrics

app = FastAPI(title="AIEDH matching API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db = match._db

STUDENT_COLS = ["student_id", "name", "school", "major", "year", "situation", "stage",
                "interests", "theme", "online", "is_synthetic"]


# ---------- 3D layout for the judge site ----------
# Positions are precomputed by compute_layout.py and stored as x, y, z columns in Tiger,
# so no embeddings are downloaded during requests. layout.json holds the projection
# used to place live callers in the same space.

@lru_cache(maxsize=1)
def _projection():
    import json
    with open("layout.json") as f:
        p = json.load(f)
    return np.array(p["mean"]), np.array(p["components"]), np.array(p["scale"])


def _place(student_id):
    """Computes and saves x, y, z for one student (a live caller) from their own vector."""
    row = db().execute("SELECT embedding_sf FROM students WHERE student_id = %s", (student_id,)).fetchone()
    if not row or row[0] is None:
        return None
    mean, comps, scale = _projection()
    p = ((np.asarray(row[0], dtype=float) - mean) @ comps.T) / scale
    p = np.clip(p, -1.2, 1.2).round(4).tolist()
    db().execute("UPDATE students SET x = %s, y = %s, z = %s WHERE student_id = %s", (*p, student_id))
    return p


def _xyz_for(ids):
    """x, y, z for a list of student ids, in one small query."""
    if not ids:
        return {}
    rows = db().execute("SELECT student_id, x, y, z FROM students WHERE student_id = ANY(%s)", (list(ids),)).fetchall()
    return {r[0]: [r[1], r[2], r[3]] if r[1] is not None else None for r in rows}


def _student_dict(row):
    d = dict(zip(STUDENT_COLS, row[:len(STUDENT_COLS)]))
    x, y, z = row[len(STUDENT_COLS):]
    d["xyz"] = [x, y, z] if x is not None else None
    return d


def _with_positions(result, student_id):
    result["xyz"] = _place(student_id) if result.get("embedded") else _xyz_for([student_id]).get(student_id)
    pos = _xyz_for([m["student_id"] for m in result["matches"]])
    for m in result["matches"]:
        m["xyz"] = pos.get(m["student_id"])
    return result


# ---------- request bodies ----------

class StartCall(BaseModel):
    call_id: str
    phone: Optional[str] = None
    student_id: Optional[str] = None


class UpdateProfile(BaseModel):
    student_id: str
    call_id: Optional[str] = None
    fields: Dict[str, str]


class Turn(BaseModel):
    student_id: str
    call_id: Optional[str] = None
    package: dict          # Muse Spark's per-turn package, unchanged


class EndCall(BaseModel):
    student_id: str
    call_id: Optional[str] = None


class Deliver(BaseModel):
    student_a: str
    student_b: str
    score: float
    call_id: Optional[str] = None
    other_call_id: Optional[str] = None
    method: str = "in_app"           # in_app | sms | call_merge
    intro_text: Optional[str] = None


class ChatMessage(BaseModel):
    sender_id: str
    body: str


# ---------- calls (Person 2) ----------

@app.post("/call/start")
def call_start(req: StartCall):
    return {"student_id": match.start_call(req.call_id, phone=req.phone, student_id=req.student_id)}


@app.post("/call/update")
def call_update(req: UpdateProfile):
    try:
        result = match.update_profile(req.student_id, req.fields, call_id=req.call_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _with_positions(result, req.student_id)   # xyz = where the caller's dot lands


@app.post("/call/turn")
def call_turn(req: Turn):
    """Send Muse Spark's package after every caller turn. Returns matches, plan, and crisis level."""
    try:
        result = match.apply_package(req.student_id, req.package, call_id=req.call_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _with_positions(result, req.student_id)


@app.post("/call/record")
def call_record(record: dict):
    """
    Send the whole end-of-call record (call_id, caller_phone, profile, summary, crisis, ...).
    Saves the caller to the waiting pool and returns their best matches. Transcript is ignored.
    """
    result = match.ingest_call(record)
    return _with_positions(result, result["student_id"])


@app.post("/call/end")
def call_end(req: EndCall):
    match.end_call(req.student_id, call_id=req.call_id)
    return {"ok": True}


@app.post("/deliver")
def deliver_match(req: Deliver):
    return delivery.deliver(req.student_a, req.student_b, req.score, call_id=req.call_id,
                            method=req.method, intro_text=req.intro_text,
                            other_call_id=req.other_call_id)


# ---------- students (Nathan + Person 4) ----------

@app.get("/students")
def students(limit: int = 2000):
    """Every student with x, y, z for the 3D view."""
    rows = db().execute(
        f"SELECT {', '.join(STUDENT_COLS)}, x, y, z FROM students "
        "WHERE embedding_sf IS NOT NULL ORDER BY is_synthetic, student_id LIMIT %s", (limit,)
    ).fetchall()
    return [_student_dict(r) for r in rows]


@app.get("/students/{student_id}")
def student(student_id: str):
    row = db().execute(
        f"SELECT {', '.join(STUDENT_COLS)}, x, y, z FROM students WHERE student_id = %s", (student_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "no such student")
    return _student_dict(row)


@app.get("/students/{student_id}/matches")
def student_matches(student_id: str):
    """Who this student was matched with, the score, how, and the intro (the 'why you matched')."""
    rows = db().execute(
        """
        SELECT m.match_id, m.score, m.method, m.intro_text, m.created_at,
               o.student_id, o.name, o.school, o.situation, o.stage, o.interests,
               c.chat_id
        FROM matches m
        JOIN students o ON o.student_id = CASE WHEN m.student_a = %(s)s THEN m.student_b ELSE m.student_a END
        LEFT JOIN chats c ON c.match_id = m.match_id
        WHERE m.student_a = %(s)s OR m.student_b = %(s)s
        ORDER BY m.created_at DESC
        """, {"s": student_id}).fetchall()
    keys = ["match_id", "score", "method", "intro", "created_at", "other_id", "other_name",
            "other_school", "other_situation", "other_stage", "other_interests", "chat_id"]
    return [dict(zip(keys, r)) for r in rows]


@app.get("/students/{student_id}/nearby")
def student_nearby(student_id: str, k: int = 10):
    """
    Read-only: the k students closest to this one, with scores and xyz, for the judge site's
    people map. Same search as a call turn, but writes nothing and logs no events.
    Like matching, it skips people this student was already matched with.
    """
    row = db().execute(
        "SELECT embedding_sf IS NOT NULL, embedding IS NOT NULL FROM students WHERE student_id = %s",
        (student_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such student")
    if not row[0] and not row[1]:
        return {"student_id": student_id, "embedder": None, "threshold": None, "matches": [], "live_match": None}
    col = "embedding_sf" if row[0] else "embedding"
    matches, live = match.search(student_id, k=max(1, min(k, 50)), col=col)
    pos = _xyz_for([m["student_id"] for m in matches])
    for m in matches:
        m["xyz"] = pos.get(m["student_id"])
    return {"student_id": student_id, "embedder": "snowflake" if col == "embedding_sf" else "local",
            "threshold": match.THRESHOLDS[col], "matches": matches, "live_match": live}


# ---------- SerpApi keyword expansion (judge site) ----------
# The key stays on this server (SERPAPI_KEY in matching/.env) and never reaches a browser.
# Only a scrubbed situation phrase is searched: no names, schools, numbers, emails, or transcripts.

SERP_ENGINES = [
    # (id, label, engine, query template)
    ("autocomplete", "Google Autocomplete", "google_autocomplete", "{q}"),
    ("google", "Google Search", "google", "{q} college student"),
    ("bing", "Bing", "bing", "{q} college student"),
    ("duckduckgo", "DuckDuckGo", "duckduckgo", "{q} college student"),
]
_NAME_FILLER = {"of", "at", "the", "and", "in", "for", "de", "la"}  # kept when stripping a school's words
_serp = {"cache": {}, "used": 0}
_serp_lock = threading.Lock()


def _serp_limit():
    return int(os.environ.get("SERPAPI_MAX_CALLS", "40"))


def _search_phrase(situation, name=None, school=None):
    """The caller's situation, cut down to a general phrase that is safe to send to a search engine."""
    text = situation or ""
    text = re.sub(r"\S+@\S+|https?://\S+|\+?\d[\d\s().-]{5,}\d|\d+", " ", text)
    for private in (name, school):
        for word in re.findall(r"[A-Za-z]{2,}", private or ""):
            if word.lower() not in _NAME_FILLER:
                text = re.sub(rf"\b{re.escape(word)}\b", " ", text, flags=re.I)
    words = re.sub(r"[^\w\s'-]", " ", text).split()
    return " ".join(words[:10]).lower()


def _serp_phrases(body, query):
    """Short queries the engine suggested (the same buckets the judge site used before). Article titles are not phrases."""
    phrases, skip = [], {query.lower()}
    for bucket, field in (("suggestions", "value"), ("related_searches", "query"), ("related_questions", "question")):
        for item in body.get(bucket) or []:
            text = (item if isinstance(item, str) else (item or {}).get(field) or "").strip()
            if not 4 <= len(text) <= 70 or text.lower() in skip or any(p.lower() == text.lower() for p in phrases):
                continue
            phrases.append(text)
            if len(phrases) >= 6:
                return phrases
    return phrases


def _serp_search(job, phrase, key):
    import requests

    sid, label, engine, template = job
    query = template.format(q=phrase)
    out = {"id": sid, "label": label, "engine": engine, "query": query, "phrases": [], "error": None,
           "receipt": None, "cached": False}
    with _serp_lock:
        hit = _serp["cache"].get((engine, query))
        if hit is None and _serp["used"] >= _serp_limit():
            out["error"] = f"Stopped at {_serp_limit()} SerpApi calls for this server run"
            return out
        if hit is None:
            _serp["used"] += 1
    if hit is not None:
        return dict(hit, cached=True)
    try:
        r = requests.get("https://serpapi.com/search.json",
                         params={"engine": engine, "q": query, "api_key": key}, timeout=20)
        body = r.json()
    except Exception as e:
        out["error"] = re.sub(r"api_key=[^&\s\"']+", "api_key=hidden", str(e).splitlines()[0])
        return out
    meta = body.get("search_metadata") or {}
    out["receipt"] = {"id": meta.get("id", ""), "status": meta.get("status", ""),
                      "created_at": meta.get("created_at", ""), "seconds": meta.get("total_time_taken")}
    if body.get("error"):
        out["error"] = str(body["error"])
    else:
        out["phrases"] = _serp_phrases(body, query)
        if not out["phrases"]:
            out["error"] = "No expansion phrases in the response."
        with _serp_lock:
            _serp["cache"][(engine, query)] = dict(out)
    return out


@app.get("/students/{student_id}/expansion")
def student_expansion(student_id: str):
    """
    SerpApi keyword expansion for this student's situation, for the judge site.
    Searches a few engines for related phrases. Repeat requests are served from memory,
    and the server stops after SERPAPI_MAX_CALLS (default 40) real searches per run.
    """
    row = db().execute("SELECT situation, theme, name, school FROM students WHERE student_id = %s",
                       (student_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such student")
    phrase = _search_phrase(row[0] or row[1], name=row[2], school=row[3])
    key = os.environ.get("SERPAPI_KEY", "").strip().strip('"')
    result = {"student_id": student_id, "query": phrase, "configured": bool(key), "engines": [],
              "calls_used": _serp["used"], "calls_limit": _serp_limit()}
    if not phrase:
        return result
    if not key:
        result["engines"] = [{"id": j[0], "label": j[1], "engine": j[2], "query": j[3].format(q=phrase), "phrases": [],
                              "error": "SERPAPI_KEY is not set in matching/.env", "receipt": None, "cached": False}
                             for j in SERP_ENGINES]
        return result
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(len(SERP_ENGINES)) as pool:
        result["engines"] = list(pool.map(lambda job: _serp_search(job, phrase, key), SERP_ENGINES))
    result["calls_used"] = _serp["used"]
    return result


@app.get("/pairs")
def pairs(limit: int = 500):
    """Every match as a line between two students, for the people graph."""
    rows = db().execute(
        "SELECT student_a, student_b, score, method, created_at FROM matches ORDER BY created_at DESC LIMIT %s",
        (limit,)).fetchall()
    return [dict(zip(["a", "b", "score", "method", "created_at"], r)) for r in rows]


# ---------- chats (Person 4) ----------

@app.get("/chats/{chat_id}/messages")
def chat_messages(chat_id: str, after_id: int = 0):
    """Poll this with after_id = last message id you have, to get only new messages."""
    rows = db().execute(
        "SELECT id, sender_id, body, created_at FROM chat_messages WHERE chat_id = %s AND id > %s ORDER BY id",
        (chat_id, after_id)).fetchall()
    return [dict(zip(["id", "sender_id", "body", "created_at"], r)) for r in rows]


@app.post("/chats/{chat_id}/messages")
def chat_send(chat_id: str, msg: ChatMessage):
    chat = db().execute("SELECT student_a, student_b FROM chats WHERE chat_id = %s", (chat_id,)).fetchone()
    if not chat:
        raise HTTPException(404, "no such chat")
    if msg.sender_id not in chat:
        raise HTTPException(403, "sender is not in this chat")
    delivery._post(chat_id, msg.sender_id, msg.body)

    # Demo: if the other person is a fake student, Gemini answers as them a few seconds later
    other = chat[1] if msg.sender_id == chat[0] else chat[0]
    synthetic = db().execute("SELECT is_synthetic FROM students WHERE student_id = %s", (other,)).fetchone()
    if synthetic and synthetic[0]:
        import threading

        def reply():
            try:
                time.sleep(2)  # feels more human than an instant answer
                delivery.demo_reply(chat_id, other)
            except Exception as e:
                print("demo reply failed:", str(e).splitlines()[0])
        threading.Thread(target=reply, daemon=True).start()
    return {"ok": True}


# ---------- judge site: live feed + metrics (Nathan) ----------

@app.get("/events")
def events(minutes: int = 10, call_id: Optional[str] = None):
    """Recent real (non-synthetic) call events, newest last. Poll every second or two."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    q = ("SELECT time, call_id, student_id, event, theme, detail, value FROM call_events "
         "WHERE time > %s AND NOT coalesce(detail->>'synthetic', 'false')::boolean")
    params: List = [since]
    if call_id:
        q += " AND call_id = %s"
        params.append(call_id)
    rows = db().execute(q + " ORDER BY time", params).fetchall()
    return [dict(zip(["time", "call_id", "student_id", "event", "theme", "detail", "value"], r)) for r in rows]


_metrics_cache = {"at": 0.0, "data": None}


@app.get("/metrics")
def metrics():
    """Everything for the Tiger panel. Cached for 60 seconds."""
    if time.time() - _metrics_cache["at"] < 60 and _metrics_cache["data"]:
        return _metrics_cache["data"]
    from bench import search_speed
    data = {
        "time_to_human": tiger_metrics.time_to_human_daily(),
        "calls_by_theme": tiger_metrics.calls_by_theme(),
        "compression": tiger_metrics.compression_stats(),
        "forecast": tiger_metrics.forecast_board(),
        "search_speed": search_speed(runs=5),
        "note": "Call history is synthetic demo data.",
    }
    _metrics_cache.update(at=time.time(), data=data)
    return data


# ---------- phone app (in-app "did I match?" instead of SMS) ----------

def _sid_for_phone(phone):
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        raise HTTPException(400, "enter a full phone number")
    return f"ph_{digits[-10:]}"


@app.get("/app/status")
def app_status(phone: str):
    """
    What the phone app shows after a call. Poll every few seconds.
    state: no_calls | waiting | matched | crisis
    """
    sid = _sid_for_phone(phone)
    row = db().execute(
        "SELECT name, school, year, major, situation, stage, wants, interests, crisis, safety_flag, "
        "online, email, last_seen FROM students WHERE student_id = %s", (sid,)).fetchone()
    if not row:
        return {"state": "no_calls", "student_id": sid}
    me = dict(zip(["name", "school", "year", "major", "situation", "stage", "wants",
                   "interests", "crisis", "safety_flag", "online", "email", "last_seen"], row))
    if me.pop("safety_flag") or me["crisis"] in ("concern", "urgent"):
        return {"state": "crisis", "student_id": sid, "me": me}

    matches = student_matches(sid)
    if not matches:
        # SerpApi can fill "resources" later (real links at the caller's school)
        return {"state": "waiting", "student_id": sid, "me": me, "resources": []}

    m = matches[0]
    other = db().execute("SELECT is_synthetic FROM students WHERE student_id = %s", (m["other_id"],)).fetchone()
    mine = {x.strip().lower() for x in (me["interests"] or "").split(",") if x.strip()}
    theirs = {x.strip().lower() for x in (m["other_interests"] or "").split(",") if x.strip()}
    return {
        "state": "matched",
        "student_id": sid,
        "me": me,
        "match": {
            "chat_id": m["chat_id"],
            "name": m["other_name"],
            "school": m["other_school"],
            "situation": m["other_situation"],
            "stage": m["other_stage"],
            "shared_interests": sorted(mine & theirs),
            "intro": m["intro"],
            "demo_student": bool(other and other[0]),
            "matched_at": m["created_at"],
            "similarity": "Really similar" if (m["score"] or 0) >= 0.88 else "Similar",
        },
        "all_matches": len(matches),
    }


@app.get("/app/matches")
def app_matches(phone: str):
    """Everyone this phone's student has matched with, newest first, with the last message."""
    sid = _sid_for_phone(phone)
    out = []
    for m in student_matches(sid):
        last = None
        if m["chat_id"]:
            r = db().execute(
                "SELECT sender_id, body, created_at FROM chat_messages "
                "WHERE chat_id = %s AND sender_id NOT IN ('system') ORDER BY id DESC LIMIT 1",
                (m["chat_id"],)).fetchone()
            if r:
                last = {"from_me": r[0] == sid, "from_ai": r[0] == "aiedh", "body": r[1], "at": r[2]}
        demo = db().execute("SELECT is_synthetic FROM students WHERE student_id = %s", (m["other_id"],)).fetchone()
        out.append({"chat_id": m["chat_id"], "name": m["other_name"], "school": m["other_school"],
                    "matched_at": m["created_at"], "last": last, "demo_student": bool(demo and demo[0])})
    return out


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/app/")


@app.get("/health")
def health():
    return {"ok": True, "students": db().execute("SELECT count(*) FROM students").fetchone()[0]}


# Serve the phone app (matching/app/) at /app/ so everything is one URL.
# Mounted last so it doesn't hide /app/status.
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount("/app", StaticFiles(directory="app", html=True), name="phone_app")