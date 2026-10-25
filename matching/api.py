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


@app.get("/health")
def health():
    return {"ok": True, "students": db().execute("SELECT count(*) FROM students").fetchone()[0]}