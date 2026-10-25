"""Reply engines: produce Aide's spoken line (streamed) plus the turn's analysis package.

The interface keeps the spoken reply separable from the analysis:

    reply = engine.start(ctx)        # starts work, returns immediately
    async for text in reply.chunks(): ...   # spoken line, as it arrives
    package = await reply.package    # learned/confidence/summary/crisis, or None

SparkCombinedEngine is the current implementation: one Muse Spark call whose
JSON has "say" first, streamed to TTS while the rest is still generating. A
split design (a fast model streams plain-text say while Spark returns the
package with strict json_schema) would be another ReplyEngine; the package
future may then resolve after the spoken line, and Conversation applies it
whenever it lands.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import AsyncOpenAI

from .planner import MOVES
from .prompts import TurnContext, combined_messages
from .state import CONF_FIELDS, CRISIS_LEVELS, ENUMS, LIST_FIELDS, PROFILE_FIELDS, THEMES

log = logging.getLogger("brain")

SPARK_BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = os.environ.get("SPARK_MODEL", "") or "muse-spark-1.3"
SPARK_REASONING_EFFORT = os.environ.get("SPARK_REASONING_EFFORT", "") or "minimal"
# Spark always reasons, and reasoning tokens count against this limit. With 600,
# "low" effort sometimes produced no visible output at all.
SPARK_MAX_TOKENS = int(os.environ.get("SPARK_MAX_TOKENS", "") or 1500)
PROMPT_CACHE_KEY = "aide"


# ---- package -----------------------------------------------------------------

@dataclass
class Package:
    """Everything in a turn's reply except the spoken line, validated."""
    ready_for_next_phase: bool = False
    move: str | None = None                                 # the move the model says it made
    learned: dict[str, Any] = field(default_factory=dict)   # only non-null, valid values
    confidence: dict[str, float] = field(default_factory=dict)
    summary: str | None = None
    crisis_level: str = "none"
    crisis_reason: str = ""


def _clean_str(value: Any, limit: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] or None


def validate_package(data: Any) -> Package | None:
    if not isinstance(data, dict):
        return None
    pkg = Package(ready_for_next_phase=data.get("ready_for_next_phase") is True)
    move = data.get("move")
    if isinstance(move, str) and move.strip().upper() in MOVES:
        pkg.move = move.strip().upper()

    learned = data.get("learned") if isinstance(data.get("learned"), dict) else {}
    for name in PROFILE_FIELDS:
        value = learned.get(name)
        if value is None:
            continue
        if name in LIST_FIELDS:
            if not isinstance(value, list):
                continue
            items = [s for s in (_clean_str(v, 60) for v in value) if s]
            if name == "themes":
                items = [t for t in dict.fromkeys(i.lower() for i in items) if t in THEMES]
            elif name == "partner_traits":
                items = list(dict.fromkeys(items))[:5]
            else:
                items = items[:3]
            if items:
                pkg.learned[name] = items
        elif name in ENUMS:
            value = _clean_str(value, 40)
            if value and value.lower() in ENUMS[name]:
                pkg.learned[name] = value.lower()
        else:
            value = _clean_str(value)
            if value:
                pkg.learned[name] = value

    conf = data.get("confidence") if isinstance(data.get("confidence"), dict) else {}
    for name in CONF_FIELDS:
        value = conf.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            pkg.confidence[name] = min(1.0, max(0.0, float(value)))

    pkg.summary = _clean_str(data.get("summary"), 1200)

    crisis = data.get("crisis") if isinstance(data.get("crisis"), dict) else {}
    level = crisis.get("level")
    if isinstance(level, str) and level.lower() in CRISIS_LEVELS:
        pkg.crisis_level = level.lower()
    pkg.crisis_reason = _clean_str(crisis.get("reason"), 200) or ""
    return pkg


def parse_package(raw: str) -> tuple[str | None, Package | None]:
    """(say, package) from the full reply text; either may be None."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None, None
    say = _clean_str(data.get("say"), 600) if isinstance(data, dict) else None
    return say, validate_package(data)


# ---- incremental "say" extraction ------------------------------------------

_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": " ", "t": " ", "r": "", "b": "", "f": ""}


class SayExtractor:
    """Pulls the top-level "say" string out of streaming JSON, decoding escapes
    (including ones split across chunks) as the text arrives.

    If the reply doesn't start with '{' (or a ``` fence), the model answered in
    plain text; that text is the spoken line, up to any '{' (models sometimes
    write a sentence and then the JSON; the JSON must never be spoken).
    """

    def __init__(self) -> None:
        self.mode = "start"        # start | fence | json | say | after | plain
        self.say = ""
        self.done = False          # closing quote of "say" seen
        self._depth = 0
        self._in_str = False
        self._esc = False
        self._key: list[str] | None = None   # collecting a depth-1 key
        self._expect_key = False
        self._last_key: str | None = None
        self._colon = False                  # saw ':' after the last key
        self._pending = ""                   # undecoded tail of the say value
        self._fence = ""

    def feed(self, chunk: str) -> str:
        """Feed raw model text; returns newly decoded spoken text."""
        out: list[str] = []
        for i, ch in enumerate(chunk):
            if self.mode == "start":
                if ch.isspace():
                    continue
                if ch == "{":
                    self.mode = "json"
                    self._depth = 1
                    self._expect_key = True
                elif ch == "`":
                    self.mode = "fence"
                else:
                    self.mode = "plain"
                    out.append(self._plain(chunk[i:]))
                    break
            elif self.mode == "fence":
                self._fence += ch
                if ch == "{":
                    self.mode = "json"
                    self._depth = 1
                    self._expect_key = True
                elif len(self._fence) > 20:          # not a JSON fence after all
                    self.mode = "plain"
                    out.append(self._plain(self._fence))
            elif self.mode == "json":
                self._scan(ch)
            elif self.mode == "say":
                self._pending += ch
                out.append(self._decode())
            elif self.mode == "plain":
                out.append(self._plain(chunk[i:]))
                break
            # "after": ignore the rest
        text = "".join(out)
        self.say += text
        return text

    def _plain(self, text: str) -> str:
        cut = text.find("{")
        if cut < 0:
            return text
        self.mode = "after"
        return text[:cut]

    def finish(self) -> str:
        """Call at end of stream. Returns the full spoken line."""
        if self.mode == "plain" or (self.mode == "after" and not self.done):
            self.say = self.say.strip()
            self.done = bool(self.say)
        return self.say

    def _scan(self, ch: str) -> None:
        if self._in_str:
            if self._esc:
                self._esc = False
                if self._key is not None:
                    self._key.append(ch)
            elif ch == "\\":
                self._esc = True
            elif ch == '"':
                self._in_str = False
                if self._key is not None:
                    self._last_key = "".join(self._key)
                    self._key = None
                    self._colon = False
            elif self._key is not None:
                self._key.append(ch)
            return
        if ch == '"':
            if self._depth == 1 and self._colon and self._last_key == "say":
                self.mode = "say"
                return
            self._in_str = True
            if self._depth == 1 and self._expect_key:
                self._key = []
                self._expect_key = False
            self._colon = False
        elif ch == ":" and self._depth == 1:
            self._colon = True
        elif ch in "{[":
            self._depth += 1
            self._colon = False
        elif ch in "}]":
            self._depth -= 1
        elif ch == "," and self._depth == 1:
            self._expect_key = True
            self._last_key = None
            self._colon = False
        elif not ch.isspace():
            self._colon = False   # a non-string value

    def _decode(self) -> str:
        out: list[str] = []
        p = self._pending
        i = 0
        while i < len(p):
            ch = p[i]
            if ch == '"':
                self.mode = "after"
                self.done = True
                i = len(p)
                break
            if ch != "\\":
                out.append(ch)
                i += 1
                continue
            if i + 1 >= len(p):
                break                                   # escape split across chunks
            kind = p[i + 1]
            if kind != "u":
                out.append(_SIMPLE_ESCAPES.get(kind, kind))
                i += 2
                continue
            if i + 6 > len(p):
                break
            try:
                code = int(p[i + 2:i + 6], 16)
            except ValueError:
                i += 6
                continue
            if 0xD800 <= code < 0xDC00:                 # high surrogate: need the pair
                if i + 12 > len(p):
                    break
                try:
                    low = int(p[i + 8:i + 12], 16) if p[i + 6:i + 8] == "\\u" else -1
                except ValueError:
                    low = -1
                if 0xDC00 <= low < 0xE000:
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                    i += 12
                else:
                    i += 6
                continue
            out.append(chr(code))
            i += 6
        self._pending = p[i:] if self.mode == "say" else ""
        return "".join(out)


# ---- narration guard ----------------------------------------------------------

# A first sentence that describes the reply instead of being it ("I'll ask what part
# feels heaviest.", "crafting a warm follow-up question"). A question is never narration.
_NARRATION = re.compile(r"\b(i'?ll|i will|i'?m going to)\b|\b(crafting|now hearing|pivot\w*|switching to|refocus\w*)\b|"
                        r"\basking (what|about|how|whether|if|for)\b|"
                        r"\b(follow-up|either-or) question\b|\boffer (\w+ )?(options|choices)\b", re.I)
_SENTENCE_END = re.compile(r"[.!?](\s|$)")


def narrates(text: str) -> bool:
    return "?" not in (text or "") and bool(_NARRATION.search(text or ""))


# ---- reply + engine interface -------------------------------------------------

class Reply:
    """One turn's reply in flight. The spoken line streams via chunks(); the
    analysis package resolves via the `package` future (None if unavailable)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.package: asyncio.Future[Package | None] = asyncio.get_running_loop().create_future()
        self.say = ""
        self.say_done = False
        self.error: str | None = None
        self.timings: dict[str, float] = {}     # time.monotonic() stamps
        self.task: asyncio.Task | None = None

    async def chunks(self) -> AsyncIterator[str]:
        while True:
            text = await self._queue.get()
            if text is None:
                return
            yield text

    def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()

    # ---- for engines ----

    def stamp(self, name: str) -> None:
        self.timings.setdefault(name, time.monotonic())

    def push(self, text: str) -> None:
        if text and not self.say_done:
            self.stamp("first_say")
            self.say += text
            self._queue.put_nowait(text)

    def end_say(self) -> None:
        if not self.say_done:
            self.say_done = True
            self.stamp("say_done")
            self._queue.put_nowait(None)

    def resolve(self, package: Package | None) -> None:
        self.end_say()
        self.stamp("done")
        if not self.package.done():
            self.package.set_result(package)


class ReplyEngine(Protocol):
    def start(self, ctx: TurnContext) -> Reply: ...
    async def warm(self) -> None: ...


_client: AsyncOpenAI | None = None


def spark_client() -> AsyncOpenAI:
    """One shared client (and HTTP connection pool) for every call."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=SPARK_BASE_URL, api_key=os.environ.get("MODEL_API_KEY", ""),
                              timeout=20.0, max_retries=0)
    return _client


class SparkCombinedEngine:
    """One Muse Spark call per turn; "say" is the first JSON key and is streamed out early."""

    def __init__(self, *, client: AsyncOpenAI | None = None, model: str = SPARK_MODEL,
                 effort: str = SPARK_REASONING_EFFORT, max_tokens: int = SPARK_MAX_TOKENS) -> None:
        self.client = client or spark_client()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    async def warm(self) -> None:
        """Open the TLS connection before the first turn needs it."""
        try:
            await self.client.models.list()
        except Exception as exc:
            log.warning("Spark warm-up failed: %r", exc)

    def start(self, ctx: TurnContext) -> Reply:
        reply = Reply()
        reply.task = asyncio.create_task(self._run(ctx, reply), name="spark-turn")
        return reply

    async def _run(self, ctx: TurnContext, reply: Reply) -> None:
        reply.stamp("request")
        messages = combined_messages(ctx)
        package: Package | None = None
        try:
            for attempt in (1, 2, 3):
                result = await self._attempt(messages, reply, check=attempt < 3)
                if result == "narrated":
                    # Nothing was spoken yet: ask again. The same request, since a note
                    # about the slip just gets narrated too ("I'm fixing that now...").
                    continue
                package = result
                break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reply.error = f"{type(exc).__name__}: {exc}"
            log.error("Spark call failed: %s", reply.error)
        finally:
            reply.resolve(package)

    async def _attempt(self, messages: list[dict[str, str]], reply: Reply, *,
                       check: bool) -> Package | None | str:
        """One streamed request. With `check`, the first sentence is held back and, if it
        describes what Aide would say instead of saying it, nothing is spoken and
        "narrated" is returned. (TTS waits for the first sentence anyway.)"""
        extractor = SayExtractor()
        raw: list[str] = []
        finish = None
        stream = None
        held, released = "", not check
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                reasoning_effort=self.effort,
                max_completion_tokens=self.max_tokens,
                prompt_cache_key=PROMPT_CACHE_KEY,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                delta = choice.delta.content or ""
                if not delta:
                    continue
                reply.stamp("first_token")
                raw.append(delta)
                text = extractor.feed(delta)
                if not released:
                    held += text
                    if not (_SENTENCE_END.search(held) or extractor.done):
                        continue
                    if narrates(held):
                        log.warning("Spark narrated instead of speaking, asking again: %r", held)
                        return "narrated"
                    released, text = True, held
                reply.push(text)
                if extractor.done:
                    reply.end_say()
            extractor.finish()
            if not released:
                if narrates(extractor.say):
                    log.warning("Spark narrated instead of speaking, asking again: %r", extractor.say)
                    return "narrated"
                reply.push(extractor.say)
            full = "".join(raw)
            parsed_say, package = parse_package(full)
            if not reply.say and parsed_say:
                reply.push(parsed_say)     # extractor missed it; speak late rather than never
            if not reply.say.strip():
                reply.error = f"empty reply (finish={finish}, {len(full)} chars)"
            elif not extractor.done and package is None:
                reply.error = f"cut-off reply (finish={finish})"
            if package is None and full and extractor.mode != "plain":
                log.warning("Spark reply was not valid JSON (finish=%s): %.200r", finish, full)
            return package
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
