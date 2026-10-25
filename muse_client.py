"""Realtime client for Muse Voice Transcribe (wss://api.meta.ai/v1/asr/realtime).

Follows the meta-model-cookbook pattern (06_muse_voice/01_voice_api_fundamentals):
  1. Open the socket, send a JSON handshake as the first text frame.
     Auth rides inside the handshake, not in an HTTP header.
  2. Read the handshake ack ({"sessionId": ...}, the only frame with no "type").
  3. Stream raw 16-bit LE mono PCM as binary frames, in real time.
  4. To finish: send {"type": "endStream"} and keep reading until the server
     closes with 1000. Closing early can discard pending events.

Turn lifecycle in ENDPOINTING mode:
  speechStart(turnId) -> transcript partials (no turnId; they belong to the
  turn opened by the latest speechStart) -> speechEnd(turnId) ->
  speechComplete(turnId, transcript). The final text is in speechComplete.

One MuseStream == one realtime session. The phone server makes one per call;
test_muse_mic.py makes one for the laptop mic.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

log = logging.getLogger("muse")

MUSE_URL = "wss://api.meta.ai/v1/asr/realtime"
MUSE_MODEL = "muse-voice-transcribe-1.0"
SAMPLE_RATES = {"PCM_16KHZ": 16000, "PCM_24KHZ": 24000}

# The server closes with 1008 if more than 5 s of audio is backlogged. If the
# socket stalls, drop the oldest audio rather than hoarding it.
MAX_BACKLOG_SECONDS = 4.0
# How long to wait for the server to flush and close after endStream.
FLUSH_TIMEOUT_SECONDS = 10.0

CLOSE_CODES = {
    1000: "normal completion",
    1008: "bad request, or audio paced too slowly/too fast (no handshake in 10s, >5s backlog, or <real-time ingress for 10s)",
    1011: "server error, or max session duration (60 min) reached",
    1013: "rate limited; back off and reconnect",
}

# The event loop only keeps weak references to tasks; hold sessions that are
# still flushing after their caller has moved on.
_RUNNING: set[asyncio.Task] = set()

# on_transcript(turn_id, text, final)
TranscriptCallback = Callable[[Any, str, bool], Awaitable[None]]
# on_event(kind, turn_id) for "speechStart" / "speechEnd"
EventCallback = Callable[[str, Any], Awaitable[None]]


class MuseStream:
    def __init__(
        self,
        api_key: str,
        on_transcript: TranscriptCallback,
        *,
        label: str = "muse",
        encoding: str = "PCM_16KHZ",
        mode: str = "ENDPOINTING",
        keywords: list[str] | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self._api_key = api_key
        self._on_transcript = on_transcript
        self._on_event = on_event
        self.label = label
        self.encoding = encoding
        self.mode = mode
        self.keywords = keywords

        self.session_id: str | None = None
        self.close_code: int | None = None
        self.close_reason: str = ""

        self._bytes_per_second = SAMPLE_RATES[encoding] * 2
        self._buffer: collections.deque[bytes] = collections.deque()
        self._buffered_bytes = 0
        self._wakeup = asyncio.Event()
        self._finishing = False
        self._current_turn: Any = None
        self._task: asyncio.Task | None = None

    # ---- public API --------------------------------------------------------

    def start(self) -> None:
        """Connect in the background. Audio sent before the handshake is buffered."""
        self._task = asyncio.create_task(self._run(), name=f"muse-{self.label}")
        _RUNNING.add(self._task)
        self._task.add_done_callback(_RUNNING.discard)

    def send_audio(self, pcm: bytes) -> None:
        """Queue raw PCM for sending. Never blocks."""
        if self._finishing or not pcm:
            return
        self._buffer.append(pcm)
        self._buffered_bytes += len(pcm)
        max_bytes = MAX_BACKLOG_SECONDS * self._bytes_per_second
        dropped = 0
        while self._buffered_bytes > max_bytes and self._buffer:
            self._buffered_bytes -= len(self._buffer.popleft())
            dropped += 1
        if dropped:
            log.warning("[%s] Muse send backlog > %.0fs; dropped %d old audio frames",
                        self.label, MAX_BACKLOG_SECONDS, dropped)
        self._wakeup.set()

    def finish(self) -> None:
        """Send any queued audio, then endStream. The session closes itself."""
        if not self._finishing:
            self._finishing = True
            self._wakeup.set()

    async def wait_closed(self) -> None:
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    # ---- internals ---------------------------------------------------------

    def _handshake(self) -> dict[str, Any]:
        token = self._api_key if self._api_key.startswith("Bearer ") else f"Bearer {self._api_key}"
        handshake: dict[str, Any] = {
            "mode": self.mode,
            "authorization": {"accessToken": token},
            "audioEncoding": self.encoding,
            "model": MUSE_MODEL,
            "partialMode": "CUMULATIVE",
            "emitAudioProgress": False,
        }
        # The server silently ignores unknown/empty fields, so only send when set.
        if self.keywords:
            handshake["keywords"] = self.keywords
        return handshake

    async def _run(self) -> None:
        log.info("[%s] Muse: connecting to %s (mode=%s, encoding=%s)",
                 self.label, MUSE_URL, self.mode, self.encoding)
        ws = None
        try:
            async with websockets.connect(MUSE_URL, max_size=None, open_timeout=10) as ws:
                await ws.send(json.dumps(self._handshake()))
                ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if ack.get("type") == "error":
                    log.error("[%s] Muse handshake REJECTED: %s", self.label, ack.get("message"))
                    return
                self.session_id = ack.get("sessionId")
                log.info("[%s] Muse handshake accepted, session=%s", self.label, self.session_id)

                sender = asyncio.create_task(self._send_loop(ws))
                try:
                    async for message in ws:
                        if isinstance(message, bytes):
                            continue
                        await self._handle(json.loads(message))
                except websockets.exceptions.ConnectionClosed:
                    pass  # code/reason are read from ws below
                finally:
                    sender.cancel()
                    result = (await asyncio.gather(sender, return_exceptions=True))[0]
                    if isinstance(result, Exception) and not isinstance(
                        result, websockets.exceptions.ConnectionClosed
                    ):
                        log.error("[%s] Muse audio sender failed: %r", self.label, result)
        except websockets.exceptions.InvalidStatus as exc:
            log.error("[%s] Muse connect rejected with HTTP %s", self.label, exc.response.status_code)
        except (asyncio.TimeoutError, TimeoutError):
            log.error("[%s] Muse timed out (connect or handshake ack)", self.label)
        except websockets.exceptions.ConnectionClosed:
            pass  # closed during handshake; code/reason read from ws below
        except Exception:
            log.exception("[%s] Muse session crashed", self.label)
        finally:
            if ws is not None:
                self.close_code = ws.close_code
                self.close_reason = ws.close_reason or ""
            meaning = CLOSE_CODES.get(self.close_code, "connection closed unexpectedly")
            level = logging.INFO if self.close_code == 1000 else logging.WARNING
            log.log(level, "[%s] Muse session closed: session=%s code=%s (%s)%s",
                    self.label, self.session_id, self.close_code, meaning,
                    f" reason={self.close_reason!r}" if self.close_reason else "")

    async def _send_loop(self, ws) -> None:
        sent_first = False
        while True:
            while self._buffer:
                chunk = self._buffer.popleft()
                self._buffered_bytes -= len(chunk)
                await ws.send(chunk)  # binary frame
                if not sent_first:
                    sent_first = True
                    log.info("[%s] Muse: first audio sent (%d bytes)", self.label, len(chunk))
            if self._finishing:
                break
            self._wakeup.clear()
            await self._wakeup.wait()

        await ws.send(json.dumps({"type": "endStream"}))
        log.info("[%s] Muse: endStream sent, waiting for final results", self.label)
        await asyncio.sleep(FLUSH_TIMEOUT_SECONDS)
        log.warning("[%s] Muse did not close %.0fs after endStream; closing", self.label,
                    FLUSH_TIMEOUT_SECONDS)
        await ws.close()

    async def _handle(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "speechStart":
            self._current_turn = event.get("turnId")
            log.info("[%s] Muse speechStart turn=%s", self.label, self._current_turn)
            await self._emit("speechStart", self._current_turn)
        elif kind == "transcript":
            # Partials carry no turnId: they belong to the latest speechStart.
            # In ENDPOINTING the final text comes from speechComplete, so treat
            # every transcript frame as a partial.
            text = event.get("transcript", "")
            if text:
                log.debug("[%s] Muse partial turn=%s: %s", self.label, self._current_turn, text)
                await self._on_transcript(self._current_turn, text, False)
        elif kind == "speechEnd":
            log.info("[%s] Muse speechEnd turn=%s", self.label, event.get("turnId"))
            await self._emit("speechEnd", event.get("turnId"))
        elif kind == "speechComplete":
            turn_id = event.get("turnId")
            text = (event.get("transcript") or "").strip()
            log.info("[%s] Muse FINAL turn=%s: %s", self.label, turn_id, text)
            await self._on_transcript(turn_id, text, True)
        elif kind == "error":
            log.error("[%s] Muse error event: %s (session=%s)", self.label,
                      event.get("message"), event.get("sessionId", self.session_id))
        else:
            # Unknown event types must be ignored (per docs).
            log.debug("[%s] Muse event ignored: %s", self.label, kind)

    async def _emit(self, kind: str, turn_id: Any) -> None:
        if self._on_event is None:
            return
        try:
            await self._on_event(kind, turn_id)
        except Exception:
            log.exception("[%s] on_event(%s) handler failed", self.label, kind)
