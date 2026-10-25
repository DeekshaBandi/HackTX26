"""Text to speech for calls: ElevenLabs Flash over one warm multi-context WebSocket per call.

Each utterance is its own context on the call's socket, so a barge-in only
closes that context; the socket stays open and warm for the next line.

    tts = ElevenLabsTTS(label="call …abc123")
    await tts.connect()                 # on Twilio "start", while the opening plays
    u = tts.utterance()
    u.feed("Got it, Maya. ")            # text as the model streams it
    u.end()                             # flush + close; audio keeps coming until isFinal
    async for chunk in u:               # AudioChunk(audio=mu-law 8 kHz bytes, chars=...)
        ...
    u.cancel()                          # barge-in: stop now, late audio is dropped

Behaviour checked against the live API (2026-10-24):
  - ulaw_8000 audio is raw 8 kHz mu-law, no header.
  - isFinal arrives only after close_context; flush + close_context right away
    still produces the full text.
  - close_context mid-generation stops it, but audio already in flight still
    arrives, so a cancelled utterance drops it locally.
  - With sync_alignment, each audio message carries the exact characters it
    covers, so the running character count tells how much text has played.

The fixed lines (opening, crisis, fillers, ...) are synthesized once and kept
in tts_cache/{sha}.ulaw, so a restart doesn't re-synthesize them.

    .venv/bin/python tts.py --bench          # warm-socket time to first audio
    .venv/bin/python tts.py --build-cache    # synthesize missing fixed lines
    .venv/bin/python tts.py --play opening   # play a cached line (macOS afplay)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import websockets

HERE = Path(__file__).parent
CACHE_DIR = HERE / "tts_cache"

log = logging.getLogger("tts")

ELEVEN_MODEL = os.environ.get("ELEVENLABS_MODEL", "") or "eleven_flash_v2_5"
OUTPUT_FORMAT = "ulaw_8000"
SAMPLE_RATE = 8000
INACTIVITY_TIMEOUT = 180          # seconds, the API maximum
KEEPALIVE_SECONDS = 30
# First audio is generated after 50 characters (the API minimum) unless a flush
# comes sooner; the first sentence is always flushed as soon as it ends.
CHUNK_SCHEDULE = [50, 90, 160, 250]
CACHE_CONCURRENCY = 4             # contexts at once while building the cache

# A sentence end followed by whitespace. Requiring the space keeps "maya.r" or
# "3.5" from counting.
_SENTENCE_END = re.compile(r"[.?!]+[\"')\]]*\s")


# ---- mu-law helpers -----------------------------------------------------------

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
SILENCE_BYTE = 0xFF                # mu-law zero
_QUIET = 200                       # |sample| below this counts as silence for trimming


def trim_silence(audio: bytes, keep_lead_ms: int = 20, keep_tail_ms: int = 120) -> bytes:
    """Trim leading/trailing silence from mu-law audio, keeping a short pad."""
    if not audio:
        return audio
    loud = np.flatnonzero(np.abs(ULAW_TABLE[np.frombuffer(audio, dtype=np.uint8)].astype(np.int32)) >= _QUIET)
    if loud.size == 0:
        return audio
    start = max(0, int(loud[0]) - keep_lead_ms * SAMPLE_RATE // 1000)
    end = min(len(audio), int(loud[-1]) + 1 + keep_tail_ms * SAMPLE_RATE // 1000)
    return audio[start:end]


def leading_silence_ms(audio: bytes) -> float:
    loud = np.flatnonzero(np.abs(ULAW_TABLE[np.frombuffer(audio, dtype=np.uint8)].astype(np.int32)) >= _QUIET)
    n = int(loud[0]) if loud.size else len(audio)
    return n * 1000 / SAMPLE_RATE


def duration_ms(audio: bytes) -> float:
    return len(audio) * 1000 / SAMPLE_RATE


def cut_to_word(text: str, chars: int) -> str:
    """The part of `text` that played, given the characters covered so far,
    cut back to a whole word."""
    if chars >= len(text.rstrip()):
        return text.strip()
    head = text[:max(0, chars)]
    mid_word = head and not head[-1].isspace() and (text[chars].isalnum() or text[chars] in "'-")
    if mid_word:
        head = head.rsplit(None, 1)[0] if len(head.split()) > 1 else ""
    return head.strip()


# ---- provider interface ---------------------------------------------------------

@dataclass
class AudioChunk:
    audio: bytes      # raw mu-law 8 kHz
    chars: int        # characters of the utterance's text covered so far; -1 if no alignment
    start_chars: int = 0                 # covered before this chunk
    char_ms: list[int] | None = None     # start time of each of this chunk's characters, ms into the chunk

    def chars_at(self, ms: float) -> int:
        """Characters covered once `ms` of this chunk has played (-1 if unknown)."""
        if self.char_ms is None:
            return self.chars
        if ms >= duration_ms(self.audio):
            return self.chars
        return self.start_chars + sum(1 for t in self.char_ms if t < ms)


class Utterance(Protocol):
    id: str
    text: str
    timings: dict[str, float]
    error: str | None

    def feed(self, text: str) -> None: ...
    def end(self) -> None: ...
    def cancel(self) -> None: ...
    def __aiter__(self) -> AsyncIterator[AudioChunk]: ...


class TTSProvider(Protocol):
    async def connect(self) -> None: ...
    def utterance(self) -> Utterance: ...
    async def synthesize(self, text: str) -> bytes: ...
    async def close(self) -> None: ...


# ---- ElevenLabs ------------------------------------------------------------------

class ElevenUtterance:
    """One context on the socket. feed/end/cancel never block; messages go out
    in order through the socket's writer task."""

    def __init__(self, tts: ElevenLabsTTS, context_id: str) -> None:
        self._tts = tts
        self.id = context_id
        self.text = ""                 # everything fed, exactly as sent
        self.chars = 0                 # characters covered by audio received so far
        self.audio_bytes = 0
        self.error: str | None = None
        self.cancelled = False
        self.finished = False          # isFinal seen, cancelled, or failed
        self.timings: dict[str, float] = {}   # time.monotonic() stamps
        self._held = ""                # text not sent yet (before the first flush)
        self._flushed = False
        self._ended = False
        self._started = False
        self._queue: asyncio.Queue[AudioChunk | None] = asyncio.Queue()

    def _stamp(self, name: str) -> None:
        self.timings.setdefault(name, time.monotonic())

    def _send(self, msg: dict) -> None:
        if not self._started:
            self._started = True
            msg = {**msg, "generation_config": {"chunk_length_schedule": CHUNK_SCHEDULE}}
        self._tts._post({**msg, "context_id": self.id})

    def feed(self, text: str) -> None:
        if not text or self._ended or self.finished:
            return
        self._stamp("first_text")
        self.text += text
        if self._flushed:
            self._send({"text": text})
            return
        # Hold text until the first sentence ends, then flush it so its audio
        # starts right away. The server wouldn't start before 50 characters anyway.
        self._held += text
        m = None
        for m in _SENTENCE_END.finditer(self._held):
            break
        if m:
            self._send({"text": self._held[:m.end()], "flush": True})
            self._stamp("first_flush")
            self._flushed = True
            rest, self._held = self._held[m.end():], ""
            if rest:
                self._send({"text": rest})
        elif len(self._held) >= CHUNK_SCHEDULE[0]:
            self._send({"text": self._held})
            self._held = ""
            self._flushed = True

    def end(self) -> None:
        """No more text: generate whatever is left, then close the context."""
        if self._ended or self.finished:
            return
        self._ended = True
        if not self.text.strip():
            self._finish()
            return
        held, self._held = self._held, ""
        self._send({"text": (held if held.endswith(" ") else held + " ") if held else "", "flush": True})
        self._stamp("first_flush")
        self._send({"close_context": True})

    def cancel(self) -> None:
        """Barge-in: stop now. Audio still in flight for this context is dropped."""
        if self.finished:
            return
        self.cancelled = True
        if self._started:
            self._tts._post({"context_id": self.id, "close_context": True})
        self._finish()

    def __aiter__(self) -> AsyncIterator[AudioChunk]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[AudioChunk]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk

    # ---- from the reader ----

    def _on_audio(self, audio: bytes, chars: list[str] | None, starts: list[int] | None = None) -> None:
        if self.finished or not audio:
            return
        shift = 0.0
        if "first_audio" not in self.timings:
            # Lines start with 20-100 ms of silence; skip it so the caller hears words sooner.
            trimmed = trim_silence(audio, keep_tail_ms=len(audio))
            if len(trimmed) < len(audio) and leading_silence_ms(audio) < duration_ms(audio):
                shift = duration_ms(audio) - duration_ms(trimmed)
                audio = trimmed
        self._stamp("first_audio")
        self.audio_bytes += len(audio)
        if chars is None:
            self._queue.put_nowait(AudioChunk(audio, -1))
            return
        start = self.chars
        self.chars += len("".join(chars))
        char_ms = None
        if starts is not None and len(starts) == len(chars):
            char_ms = [max(0, int(t - shift)) for t in starts]
        self._queue.put_nowait(AudioChunk(audio, self.chars, start, char_ms))

    def _finish(self, error: str | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        if error and not self.error:
            self.error = error
        self._stamp("done")
        self._tts._live.pop(self.id, None)
        self._queue.put_nowait(None)


class ElevenLabsTTS:
    """One per call. Lazily (re)connects; keeps the socket warm between lines."""

    def __init__(self, api_key: str | None = None, voice_id: str | None = None, *,
                 model: str = ELEVEN_MODEL, label: str = "tts") -> None:
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")
        self.model = model
        self.label = label
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._writer: asyncio.Task | None = None
        self._outbox: asyncio.Queue[dict | None] = asyncio.Queue()
        self._live: dict[str, ElevenUtterance] = {}
        self._connect_lock = asyncio.Lock()
        self._count = 0
        self._connects = 0
        self._closed = False

    @property
    def url(self) -> str:
        return (f"wss://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/multi-stream-input"
                f"?model_id={self.model}&output_format={OUTPUT_FORMAT}"
                f"&inactivity_timeout={INACTIVITY_TIMEOUT}&sync_alignment=true")

    # ---- public API ----

    async def connect(self) -> None:
        """Open the socket now, so the first line doesn't pay for it."""
        self._start_writer()
        await self._ensure()

    def utterance(self) -> ElevenUtterance:
        self._start_writer()
        self._count += 1
        u = ElevenUtterance(self, f"u{self._count}")
        self._live[u.id] = u
        return u

    async def synthesize(self, text: str) -> bytes:
        """Whole line to mu-law bytes, over the same socket."""
        u = self.utterance()
        u.feed(text)
        u.end()
        audio = b"".join([c.audio async for c in u])
        if u.error:
            raise RuntimeError(f"TTS failed for {text[:40]!r}: {u.error}")
        return audio

    async def close(self) -> None:
        self._closed = True
        for u in list(self._live.values()):
            u.cancel()
        self._outbox.put_nowait(None)
        if self._writer:
            await asyncio.gather(self._writer, return_exceptions=True)
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"close_socket": True}))
                await asyncio.wait_for(self._ws.close(), timeout=2)
            except Exception:
                pass
        if self._reader:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)

    # ---- internals ----

    def _start_writer(self) -> None:
        if self._writer is None and not self._closed:
            self._writer = asyncio.create_task(self._write_loop(), name=f"tts-writer-{self.label}")

    def _post(self, msg: dict) -> None:
        if not self._closed:
            self._outbox.put_nowait(msg)

    def _connected(self) -> bool:
        return self._ws is not None and self._reader is not None and not self._reader.done()

    async def _ensure(self):
        async with self._connect_lock:
            if self._connected():
                return self._ws
            if not self.api_key or not self.voice_id:
                raise RuntimeError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID missing")
            t0 = time.monotonic()
            self._ws = await websockets.connect(self.url, additional_headers={"xi-api-key": self.api_key},
                                                max_size=None, open_timeout=5)
            self._connects += 1
            log.log(logging.INFO if self._connects == 1 else logging.WARNING,
                    "[%s] TTS socket %s in %.0f ms", self.label,
                    "connected" if self._connects == 1 else "RECONNECTED", (time.monotonic() - t0) * 1000)
            self._reader = asyncio.create_task(self._read_loop(self._ws), name=f"tts-reader-{self.label}")
            return self._ws

    async def _write_loop(self) -> None:
        while True:
            try:
                msg = await asyncio.wait_for(self._outbox.get(), timeout=KEEPALIVE_SECONDS)
            except (asyncio.TimeoutError, TimeoutError):
                if not self._connected():
                    continue
                msg = {"text": "", "context_id": "keepalive"}
            if msg is None:
                return
            ctx = msg.get("context_id")
            if ctx not in self._live and ctx != "keepalive" and not msg.get("close_context"):
                continue   # its utterance was cancelled or failed
            try:
                ws = await self._ensure()
                await ws.send(json.dumps(msg))
            except Exception as exc:
                log.error("[%s] TTS send failed: %r", self.label, exc)
                self._fail_live(f"send failed: {exc!r}")

    async def _read_loop(self, ws) -> None:
        error = "socket closed"
        try:
            async for raw in ws:
                data = json.loads(raw)
                u = self._live.get(data.get("contextId"))
                if u is None:
                    continue           # cancelled, or the keepalive context
                if data.get("audio"):
                    alignment = data.get("alignment") if isinstance(data.get("alignment"), dict) else {}
                    u._on_audio(base64.b64decode(data["audio"]), alignment.get("chars"),
                                alignment.get("charStartTimesMs"))
                if data.get("isFinal"):
                    u._finish()
                elif data.get("error") or data.get("message"):
                    log.error("[%s] TTS server message: %s", self.label, data)
        except websockets.exceptions.ConnectionClosed as exc:
            error = f"socket closed ({exc.rcvd.code if exc.rcvd else '?'})"
        except Exception as exc:
            error = f"reader crashed: {exc!r}"
            log.exception("[%s] TTS reader crashed", self.label)
        finally:
            if not self._closed:
                log.warning("[%s] TTS %s (code=%s reason=%r)", self.label, error,
                            ws.close_code, ws.close_reason)
            self._fail_live(error)

    def _fail_live(self, error: str) -> None:
        for u in list(self._live.values()):
            u._finish(error)


# ---- fixed-line cache ------------------------------------------------------------

class FixedAudio:
    """Cached audio for lines that never change. Built once at startup."""

    def __init__(self, lines: dict[str, str], *, voice_id: str | None = None,
                 model: str = ELEVEN_MODEL, cache_dir: Path = CACHE_DIR) -> None:
        self.lines = dict(lines)
        self.voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")
        self.model = model
        self.cache_dir = cache_dir
        self.audio: dict[str, bytes] = {}

    def path(self, key: str) -> Path:
        ident = json.dumps([self.voice_id, self.model, OUTPUT_FORMAT, self.lines[key]])
        return self.cache_dir / f"{hashlib.sha256(ident.encode()).hexdigest()[:16]}.ulaw"

    def get(self, key: str) -> bytes:
        return self.audio[key]

    async def build(self, tts: TTSProvider | None = None) -> dict[str, int]:
        """Load cached lines; synthesize the missing ones. Returns counts."""
        self.cache_dir.mkdir(exist_ok=True)
        missing = []
        for key in self.lines:
            p = self.path(key)
            if p.exists() and p.stat().st_size > 0:
                self.audio[key] = p.read_bytes()
            else:
                missing.append(key)
        if missing:
            own = tts is None
            tts = tts or ElevenLabsTTS(label="cache")
            sem = asyncio.Semaphore(CACHE_CONCURRENCY)

            async def make(key: str) -> None:
                async with sem:
                    audio = trim_silence(await tts.synthesize(self.lines[key]))
                p = self.path(key)
                tmp = p.with_suffix(".tmp")
                tmp.write_bytes(audio)
                tmp.replace(p)
                self.audio[key] = audio
                log.info("TTS cache: %-18s %5.1fs  %s", key, duration_ms(audio) / 1000, p.name)
            try:
                await asyncio.gather(*(make(k) for k in missing))
            finally:
                if own:
                    await tts.close()
        keep = {self.path(k).name for k in self.lines}
        stale = [p for p in self.cache_dir.glob("*.ulaw") if p.name not in keep]
        for p in stale:
            p.unlink()
        return {"loaded": len(self.lines) - len(missing), "synthesized": len(missing), "pruned": len(stale)}


# ---- CLI: bench / build-cache / play ---------------------------------------------

BENCH_LINES = [
    "Got it, Maya. And which school do you go to?",
    "That sounds really lonely, especially being so far from home. How long has it felt like this?",
    "Thanks. So that's m-a-y-a-dot-r at utexas dot edu, did I get that right?",
    "Okay. What would help most, someone to listen, or someone who's been through it?",
    "Sophomore year can be a lot. What are you studying?",
]


async def _stream_like_model(u: ElevenUtterance, text: str, piece: int = 4, gap: float = 0.015) -> None:
    """Feed text the way Spark streams "say": a few characters every ~15 ms."""
    for i in range(0, len(text), piece):
        u.feed(text[i:i + piece])
        await asyncio.sleep(gap)
    u.end()


async def bench(runs: int) -> None:
    tts = ElevenLabsTTS(label="bench")
    t0 = time.monotonic()
    await tts.connect()
    print(f"connect: {(time.monotonic() - t0) * 1000:.0f} ms")
    rows = []
    for i in range(runs):
        text = BENCH_LINES[i % len(BENCH_LINES)]
        u = tts.utterance()
        feeder = asyncio.create_task(_stream_like_model(u, text))
        first = None
        audio = b""
        async for chunk in u:
            if first is None:
                first = chunk.audio
            audio += chunk.audio
        await feeder
        t = u.timings
        ttfb = (t["first_audio"] - t["first_text"]) * 1000
        flush_to_audio = (t["first_audio"] - t["first_flush"]) * 1000
        lead = leading_silence_ms(audio)
        rows.append((ttfb, flush_to_audio, lead))
        print(f"  run {i + 1}: first text -> first audio {ttfb:5.0f} ms  "
              f"(first flush -> audio {flush_to_audio:4.0f} ms)  leading silence {lead:4.0f} ms  "
              f"audio {duration_ms(audio) / 1000:.1f}s  chars {u.chars}/{len(u.text)}  {text[:36]!r}")
        await asyncio.sleep(0.3)
    # Barge-in: cancel mid-line, confirm no audio comes after.
    u = tts.utterance()
    u.feed(BENCH_LINES[1] + " " + BENCH_LINES[3])
    u.end()
    got = 0
    async for chunk in u:
        got += 1
        u.cancel()
    print(f"  cancel after first chunk: {got} chunk(s) delivered, finished={u.finished}")
    r = tts.utterance()
    r.feed("Okay. ")
    r.end()
    n = len([c async for c in r])
    print(f"  next line after cancel on same socket: {n} chunk(s), reconnects={tts._connects - 1}")
    await tts.close()
    import statistics
    print(f"median first text -> first audio: {statistics.median(r[0] for r in rows):.0f} ms; "
          f"first flush -> audio: {statistics.median(r[1] for r in rows):.0f} ms; "
          f"leading silence: {statistics.median(r[2] for r in rows):.0f} ms")


def selftest() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        failures += not cond

    class Recorder(ElevenLabsTTS):
        def __init__(self) -> None:
            super().__init__(api_key="x", voice_id="v")
            self.sent: list[dict] = []

        def _start_writer(self) -> None:
            pass

        def _post(self, msg: dict) -> None:
            self.sent.append(msg)

    async def run() -> None:
        print("feed splitting")
        t = Recorder()
        u = t.utterance()
        for piece in ["Got it, Ma", "ya. And wh", "ich school?"]:
            u.feed(piece)
        u.end()
        texts = [(m.get("text"), m.get("flush", False)) for m in t.sent if "text" in m]
        check(texts[0] == ("Got it, Maya. ", True), f"first sentence flushed alone -> {texts[0]}")
        check(texts[1] == ("And wh", False), f"rest sent unflushed -> {texts[1]}")
        check("generation_config" in t.sent[0] and "generation_config" not in t.sent[1],
              "chunk schedule only on the context's first message")
        check(t.sent[-1] == {"context_id": u.id, "close_context": True}, "end closes the context")
        check(t.sent[-2].get("flush") is True, "end flushes first")
        check(u.text == "Got it, Maya. And which school?", "text kept exactly as fed")

        t = Recorder()
        u = t.utterance()
        u.feed("so that's m-a-y-a.r at utexas dot edu")
        check(t.sent == [], f"no flush on 'a.r' or before 50 chars -> {t.sent}")
        u.feed(" and some more words to pass fifty")
        check(len(t.sent) == 1 and not t.sent[0].get("flush"), "50+ held chars sent unflushed")

        print("cancel")
        t = Recorder()
        u = t.utterance()
        u.feed("Hello there. ")
        u._on_audio(b"\x10" * 400, list("Hello "))
        u.cancel()
        u._on_audio(b"\x10" * 400, list("there. "))
        chunks = [c async for c in u]
        check(len(chunks) == 1 and chunks[0].chars == 6, "late audio after cancel dropped")
        check(t.sent[-1].get("close_context") is True and u.id not in t._live, "cancel closes context")

        print("chars_at")
        t = Recorder()
        u = t.utterance()
        u.feed("Hi there. ")
        silence, loud = bytes([SILENCE_BYTE]) * 800, bytes([0x10]) * 3200   # 100 ms + 400 ms
        u._on_audio(silence + loud, list("Hi "), [100, 200, 350])
        u._on_audio(loud, list("there. "), [0, 50, 100, 150, 200, 250, 300])
        u._finish()                       # what isFinal would do
        c1, c2 = [c async for c in u][:2]
        check(len(c1.audio) == 160 + 3200, f"first chunk's leading silence trimmed -> {len(c1.audio)} bytes")
        check(c1.char_ms == [20, 120, 270], f"char times shifted by the trim -> {c1.char_ms}")
        check((c1.chars_at(0), c1.chars_at(21), c1.chars_at(500)) == (0, 1, 3), "chars_at within chunk 1")
        check((c2.start_chars, c2.chars_at(120), c2.chars) == (3, 6, 10), "chars_at within chunk 2")
        t = Recorder()
        u = t.utterance()
        u.cancel()
        check(t.sent == [], "cancel before any text sends nothing")

    asyncio.run(run())

    print("cut_to_word")
    text = "Got it, Maya. And which school do you go to? "
    for chars, want in [(0, ""), (2, ""), (5, "Got"), (6, "Got it"), (14, "Got it, Maya."),
                        (20, "Got it, Maya. And"), (len(text), text.strip()), (999, text.strip())]:
        got = cut_to_word(text, chars)
        check(got == want, f"{chars} chars -> {got!r}")

    print("trim_silence")
    loud = bytes([0x10]) * 800
    audio = bytes([SILENCE_BYTE]) * 1600 + loud + bytes([SILENCE_BYTE]) * 4000
    out = trim_silence(audio)
    check(len(out) == 160 + 800 + 960, f"keeps 20 ms lead / 120 ms tail -> {len(out)} bytes")
    check(trim_silence(bytes([SILENCE_BYTE]) * 100) == bytes([SILENCE_BYTE]) * 100, "all-silence left alone")

    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


def _fixed() -> FixedAudio:
    from ai_operator.prompts import ALL_FIXED
    return FixedAudio(ALL_FIXED)


async def build_cache() -> None:
    fixed = _fixed()
    t0 = time.monotonic()
    counts = await fixed.build()
    print(f"{counts} in {time.monotonic() - t0:.1f}s")
    for key, audio in fixed.audio.items():
        print(f"  {key:18s} {duration_ms(audio) / 1000:5.1f}s")


async def play(key: str) -> None:
    import subprocess
    import tempfile
    import wave
    fixed = _fixed()
    await fixed.build()
    pcm = ULAW_TABLE[np.frombuffer(fixed.get(key), dtype=np.uint8)].astype("<i2").tobytes()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        with wave.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
    subprocess.run(["afplay", f.name], check=False)
    os.unlink(f.name)


def main() -> int:
    import argparse
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench", action="store_true")
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--build-cache", action="store_true")
    parser.add_argument("--play")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(name)-6s %(message)s", datefmt="%H:%M:%S")
    if args.build_cache:
        asyncio.run(build_cache())
    if args.play:
        asyncio.run(play(args.play))
    if args.bench or not (args.build_cache or args.play):
        asyncio.run(bench(args.runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
