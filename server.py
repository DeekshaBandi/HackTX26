"""Phone server: Twilio call -> Muse Voice Transcribe -> Aide (AI operator) -> caller,
with a live web dashboard.

Flow:
  POST /twilio/voice    Twilio "A call comes in" webhook, answered with TwiML
  WS   /twilio-audio    Twilio Media Stream (8 kHz mu-law), one per call, both ways:
                        caller audio in (to Muse), Aide's audio out (from ElevenLabs)
  WS   /dashboard       browser pages; we broadcast call/transcript/operator events
  GET  /                the dashboard page (dashboard.html)

Per-call conversation logic is in call_session.py and ai_operator/.

Run:  .venv/bin/python server.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import logging.handlers
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from twilio.rest import Client as TwilioClient
from starlette.websockets import WebSocketDisconnected
from twilio.twiml.voice_response import Connect, VoiceResponse

from ai_operator.brain import SparkCombinedEngine
from ai_operator.prompts import ALL_FIXED
from ai_operator.state import IDENTITY_FIELDS, CallState
from call_session import CallSession
from muse_client import MuseStream
from tts import ElevenLabsTTS, FixedAudio

HERE = Path(__file__).parent
RECORDS_DIR = HERE / "records"   # one {call_id}.json per finished call (personal data; gitignored)
load_dotenv(HERE / ".env")

MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")
# Shared secret for the dashboard page, its WebSocket, and record downloads (?token=...).
# The Twilio routes stay open. Empty means no protection (a loud warning at startup).
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "8000"))

VOICE_WEBHOOK_URL = f"{PUBLIC_URL}/twilio/voice"
STREAM_URL = PUBLIC_URL.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/twilio-audio"

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
# Also to a file, so a call can be checked after the terminal is gone.
_log_file = logging.handlers.RotatingFileHandler(LOG_DIR / "aide.log", maxBytes=5_000_000, backupCount=5)
_log_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)-9s %(message)s"))
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), _log_file],
)
log = logging.getLogger("server")
logging.getLogger("twilio.http_client").setLevel(logging.WARNING)


# ---- TwiML ----------------------------------------------------------------------

def build_twiml(call_sid: str, from_number: str) -> str:
    """The one place that decides what happens on the call.

    <Connect><Stream> makes the media stream bidirectional: we receive the
    caller's audio and send Aide's back. When we close the stream (Aide said
    goodbye), Twilio moves on to the next verb, <Hangup/>.
    """
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=STREAM_URL)
    stream.parameter(name="from_number", value=from_number)
    stream.parameter(name="call_sid", value=call_sid)
    response.append(connect)
    response.hangup()
    return str(response)


# ---- Audio: Twilio 8 kHz mu-law -> 16 kHz 16-bit LE PCM ---------------------

def _build_ulaw_table() -> np.ndarray:
    """G.711 mu-law byte -> int16 sample (same values as audioop.ulaw2lin)."""
    table = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = (((mantissa << 3) + 0x84) << exponent) - 0x84
        table[i] = -sample if u & 0x80 else sample
    return table


ULAW_TABLE = _build_ulaw_table()


class TwilioAudioConverter:
    """Stateful per call so interpolation is continuous across 20 ms frames."""

    def __init__(self) -> None:
        self._prev = 0.0

    def convert(self, payload_b64: str) -> bytes:
        ulaw = np.frombuffer(base64.b64decode(payload_b64), dtype=np.uint8)
        if ulaw.size == 0:
            return b""
        x = ULAW_TABLE[ulaw].astype(np.float32)
        prev = np.concatenate(([self._prev], x[:-1]))
        out = np.empty(x.size * 2, dtype=np.float32)
        out[0::2] = (prev + x) * 0.5  # midpoint between consecutive 8 kHz samples
        out[1::2] = x
        self._prev = float(x[-1])
        return out.astype("<i2").tobytes()


# ---- Call state + dashboard broadcast ---------------------------------------

calls: dict[str, dict[str, Any]] = {}
dashboards: set[WebSocket] = set()
# Aide's per-call state lives here, so the `calls` entries (and the snapshot) keep their shape.
sessions: dict[str, CallSession] = {}
fixed_audio = FixedAudio(ALL_FIXED)
# Replayed to a dashboard after its snapshot: the latest operator_text per line and call_record per call.
operator_lines: dict[str, dict[str, dict[str, Any]]] = {}
last_records: dict[str, dict[str, Any]] = {}
# caller_phone -> identity fields from their latest saved record that has a name (returning callers).
known_callers: dict[str, dict[str, Any]] = {}


async def broadcast(message: dict[str, Any]) -> None:
    if not dashboards:
        return
    targets = list(dashboards)
    results = await asyncio.gather(
        *(asyncio.wait_for(ws.send_json(message), timeout=2) for ws in targets),
        return_exceptions=True,
    )
    for ws, result in zip(targets, results):
        if isinstance(result, BaseException):
            dashboards.discard(ws)


def short(call_id: str) -> str:
    return f"call …{call_id[-6:]}" if call_id else "call ?"


async def call_started(call_id: str, from_number: str) -> None:
    ts = time.time()
    calls[call_id] = {
        "call_id": call_id,
        "from_number": from_number,
        "started_ts": ts,
        "ended_ts": None,
        "active": True,
        "turns": {},  # turn_id (as str) -> {"turn_id", "text", "final", "ts"}
    }
    await broadcast({"type": "call_started", "call_id": call_id, "from_number": from_number, "ts": ts})


async def call_ended(call_id: str) -> None:
    call = calls.get(call_id)
    if not call or not call["active"]:
        return
    ts = time.time()
    call["active"] = False
    call["ended_ts"] = ts
    await broadcast({"type": "call_ended", "call_id": call_id, "ts": ts})


def make_transcript_handler(call_id: str):
    async def on_transcript(turn_id: Any, text: str, final: bool) -> None:
        ts = time.time()
        call = calls.get(call_id)
        if call is not None:
            turns = call["turns"]
            existing = turns.get(str(turn_id))
            if existing and existing["final"] and not final:
                return  # late partial for a finished turn
            turns[str(turn_id)] = {"turn_id": turn_id, "text": text, "final": final, "ts": ts}
        await broadcast({
            "type": "muse_transcript",
            "call_id": call_id,
            "turn_id": turn_id,
            "text": text,
            "final": final,
            "ts": ts,
        })
    return on_transcript


def make_record_broadcaster(call_id: str):
    """call_record on every record change, coalesced to one message per 50 ms."""
    pending: list[CallState] = []

    async def flush() -> None:
        await asyncio.sleep(0.05)
        state = pending[-1]
        pending.clear()
        message = {"type": "call_record", "call_id": call_id, "record": state.record(), "ts": time.time()}
        last_records[call_id] = message
        await broadcast(message)

    def on_change(state: CallState) -> None:
        if not pending:
            asyncio.get_running_loop().create_task(flush())
        pending.append(state)
    return on_change


async def emit_operator(message: dict[str, Any]) -> None:
    """operator_text from a call session: keep it for replay, then broadcast."""
    lines = operator_lines.setdefault(message["call_id"], {})
    first = lines.get(message["turn_id"])
    # Replay with the time the line first appeared, so pages order it the same way.
    lines[message["turn_id"]] = {**message, "ts": first["ts"] if first else message["ts"]}
    await broadcast(message)


def replay_messages() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for call_id in calls:
        out.extend(operator_lines.get(call_id, {}).values())
        if call_id in last_records:
            out.append(last_records[call_id])
    return out


# ---- Call records on disk + returning callers ---------------------------------

def _caller_key(phone: str) -> str | None:
    """Only real numbers identify a caller (not 'anonymous', blocked, or empty)."""
    return phone if phone and phone.startswith("+") and phone[1:].isdigit() else None


def remember_caller(record: dict[str, Any]) -> None:
    key = _caller_key(record.get("caller_phone", ""))
    profile = record.get("profile") or {}
    if not key or not profile.get("name"):
        return
    old = known_callers.get(key)
    if old is None or record.get("started_at", "") >= old["started_at"]:
        known_callers[key] = {"started_at": record.get("started_at", ""),
                              "identity": {f: profile.get(f) for f in IDENTITY_FIELDS}}


def load_records() -> int:
    count = 0
    for path in RECORDS_DIR.glob("*.json"):
        try:
            remember_caller(json.loads(path.read_text()))
            count += 1
        except Exception as exc:
            log.warning("Skipping unreadable record %s: %r", path.name, exc)
    return count


def save_record(state: CallState) -> None:
    record = state.export()
    RECORDS_DIR.mkdir(exist_ok=True)
    path = RECORDS_DIR / f"{state.call_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    tmp.replace(path)
    remember_caller(record)
    log.info("[%s] Record saved: %s", short(state.call_id), path)


def new_call_state(call_id: str, from_number: str) -> CallState:
    state = CallState(call_id=call_id, caller_phone=from_number)
    known = known_callers.get(_caller_key(from_number) or "")
    if known:
        state.profile.update({k: v for k, v in known["identity"].items() if v})
        state.returning = True
        log.info("[%s] Returning caller: %s (last call %s)", short(call_id),
                 known["identity"].get("name"), known["started_at"])
    return state


def make_twilio_sender(ws: WebSocket):
    lock = asyncio.Lock()

    async def send(message: dict[str, Any]) -> None:
        async with lock:
            try:
                await ws.send_text(json.dumps(message))
            except (WebSocketDisconnect, WebSocketDisconnected, RuntimeError):
                pass  # call already over
    return send


def snapshot() -> dict[str, Any]:
    return {
        "type": "snapshot",
        "ts": time.time(),
        "calls": [{**c, "turns": list(c["turns"].values())} for c in calls.values()],
    }


# ---- Startup: point the Twilio number at us ---------------------------------

def configure_twilio_webhook() -> None:
    """Blocking (Twilio SDK uses requests) -- run in a thread."""
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        numbers = client.incoming_phone_numbers.list(phone_number=TWILIO_PHONE_NUMBER, limit=1)
        if not numbers:
            log.error("Twilio webhook NOT updated: %s not found on this account", TWILIO_PHONE_NUMBER)
            return
        number = numbers[0]
        updates: dict[str, Any] = {"voice_url": VOICE_WEBHOOK_URL, "voice_method": "POST"}
        if number.voice_application_sid:
            # A TwiML App on the number overrides voice_url, so detach it.
            log.warning("Twilio: number was using TwiML App %s; detaching so the webhook applies",
                        number.voice_application_sid)
            updates["voice_application_sid"] = ""
        if number.trunk_sid:
            log.warning("Twilio: number is attached to SIP trunk %s, which overrides the voice webhook",
                        number.trunk_sid)
        log.info("Twilio: previous voice webhook was %s %s", number.voice_method, number.voice_url)
        updated = number.update(**updates)
        log.info("Twilio webhook updated OK: %s -> %s %s",
                 updated.phone_number, updated.voice_method, updated.voice_url)
    except Exception as exc:
        log.error("Twilio webhook NOT updated: %r", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [k for k, v in {
        "MODEL_API_KEY": MODEL_API_KEY, "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN, "TWILIO_PHONE_NUMBER": TWILIO_PHONE_NUMBER,
        "PUBLIC_URL": PUBLIC_URL, "ELEVENLABS_API_KEY": ELEVENLABS_API_KEY,
        "ELEVENLABS_VOICE_ID": ELEVENLABS_VOICE_ID,
    }.items() if not v]
    if missing:
        log.error("Missing .env values: %s", ", ".join(missing))
    log.info("Voice webhook: %s", VOICE_WEBHOOK_URL)
    log.info("Media stream:  %s", STREAM_URL)
    if DASHBOARD_TOKEN:
        query = f"?token={quote(DASHBOARD_TOKEN, safe='')}"
        log.info("Dashboard:     http://localhost:%d/%s", PORT, query)
        if PUBLIC_URL:
            log.info("Dashboard:     %s/%s", PUBLIC_URL, query)
    else:
        for line in ["!" * 78,
                     "!!  DASHBOARD_TOKEN is not set in .env. The dashboard, its live transcripts,",
                     "!!  and every call record (names, emails, what callers said) are open to",
                     f"!!  ANYONE who has the public URL {PUBLIC_URL or '(PUBLIC_URL)'}",
                     "!!  Set DASHBOARD_TOKEN=<long random string> in .env and restart.",
                     "!" * 78]:
            log.warning(line)
        log.info("Dashboard:     http://localhost:%d/", PORT)
    log.info("Loaded %d saved call records (%d returning callers known)", load_records(), len(known_callers))
    t0 = time.monotonic()
    counts, _ = await asyncio.gather(fixed_audio.build(), asyncio.to_thread(configure_twilio_webhook))
    log.info("TTS cache ready in %.1fs: %s", time.monotonic() - t0, counts)
    yield


app = FastAPI(lifespan=lifespan)


# ---- HTTP routes ------------------------------------------------------------

def token_ok(token: str | None) -> bool:
    if not DASHBOARD_TOKEN:
        return True
    return token is not None and secrets.compare_digest(token.encode(), DASHBOARD_TOKEN.encode())


@app.get("/")
async def index(token: str | None = None) -> Response:
    if not token_ok(token):
        return Response("Forbidden: open the dashboard link printed when the server starts (it has ?token=...).",
                        status_code=403, media_type="text/plain")
    return FileResponse(HERE / "dashboard.html")


@app.get("/calls/{call_id}/record.json")
async def call_record_json(call_id: str, token: str | None = None) -> Response:
    """The call's record.json: live from memory, or from records/ for older calls."""
    if not token_ok(token):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    session = sessions.get(call_id)
    if session is not None:
        data: Any = session.state.export()
    else:
        path = RECORDS_DIR / f"{call_id}.json"
        if not call_id.replace("_", "").replace("-", "").isalnum() or not path.is_file():
            return JSONResponse({"error": "no such call"}, status_code=404)
        data = json.loads(path.read_text())
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{call_id}.json"'})


@app.post("/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    from_number = str(form.get("From", ""))
    log.info("[%s] Webhook hit: incoming call from %s (CallSid=%s)", short(call_sid), from_number, call_sid)
    twiml = build_twiml(call_sid, from_number)
    log.debug("TwiML: %s", twiml)
    return Response(content=twiml, media_type="application/xml")


# ---- Twilio media stream ----------------------------------------------------

@app.websocket("/twilio-audio")
async def twilio_audio(ws: WebSocket) -> None:
    await ws.accept()
    log.info("Twilio media WebSocket opened")
    call_id = ""
    muse: MuseStream | None = None
    session: CallSession | None = None
    converter = TwilioAudioConverter()
    frames = 0
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            event = msg.get("event")

            if event == "media":
                if muse is not None:
                    muse.send_audio(converter.convert(msg["media"]["payload"]))
                    frames += 1
                    if frames == 1:
                        log.info("[%s] Twilio: first audio frame received", short(call_id))

            elif event == "connected":
                log.info("Twilio stream connected (protocol=%s)", msg.get("protocol"))

            elif event == "start":
                start = msg.get("start", {})
                params = start.get("customParameters", {}) or {}
                call_id = start.get("callSid") or params.get("call_sid", "")
                from_number = params.get("from_number", "")
                log.info("[%s] Twilio stream started: streamSid=%s from=%s format=%s",
                         short(call_id), start.get("streamSid"), from_number, start.get("mediaFormat"))
                await call_started(call_id, from_number)
                session = CallSession(
                    new_call_state(call_id, from_number),
                    engine=SparkCombinedEngine(), tts=ElevenLabsTTS(label=short(call_id)),
                    fixed=fixed_audio, send=make_twilio_sender(ws), hangup=ws.close,
                    emit=emit_operator, on_change=make_record_broadcaster(call_id), label=short(call_id))
                sessions[call_id] = session
                dashboard_handler = make_transcript_handler(call_id)

                async def on_transcript(turn_id: Any, text: str, final: bool,
                                        session: CallSession = session) -> None:
                    await dashboard_handler(turn_id, text, final)
                    await session.on_transcript(turn_id, text, final)

                muse = MuseStream(MODEL_API_KEY, on_transcript, label=short(call_id),
                                  encoding="PCM_16KHZ", mode="ENDPOINTING", on_event=session.on_muse_event)
                muse.start()
                await session.start(start.get("streamSid", ""))

            elif event == "stop":
                log.info("[%s] Twilio stream stopped (%d frames, %.1fs of audio)",
                         short(call_id), frames, frames * 0.02)
                break

            elif event == "mark":
                if session is not None:
                    session.on_mark(msg.get("mark", {}).get("name", ""))
            else:
                log.debug("Twilio event ignored: %s", event)
    except (WebSocketDisconnect, WebSocketDisconnected):
        if session is not None and session.hung_up:
            log.info("[%s] Twilio media WebSocket closed by Aide (call ending)", short(call_id))
        else:
            log.info("[%s] Twilio media WebSocket disconnected", short(call_id))
    except Exception:
        log.exception("[%s] Twilio media handler crashed", short(call_id))
    finally:
        if muse is not None:
            muse.finish()  # endStream; the session flushes and closes on its own task
        if session is not None:
            await session.end()
            try:
                save_record(session.state)
            except Exception:
                log.exception("[%s] Could not save the call record", short(call_id))
        if call_id:
            await call_ended(call_id)


# ---- Browser dashboard ------------------------------------------------------

@app.websocket("/dashboard")
async def dashboard(ws: WebSocket) -> None:
    if not token_ok(ws.query_params.get("token")):
        log.warning("Dashboard connection refused: missing or wrong token")
        await ws.close(code=1008)   # before accept, so the handshake is refused with HTTP 403
        return
    await ws.accept()
    dashboards.add(ws)
    log.info("Dashboard connected (%d open)", len(dashboards))
    try:
        await ws.send_json(snapshot())
        for message in replay_messages():
            await ws.send_json(message)
        while True:
            await ws.receive_text()  # we don't expect messages; this detects disconnect
    except WebSocketDisconnect:
        pass
    finally:
        dashboards.discard(ws)
        log.info("Dashboard disconnected (%d open)", len(dashboards))


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=PORT, log_level="info")
