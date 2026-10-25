"""One phone call: turn-taking, playback to Twilio, barge-in, fillers, stalls, silence, hangup.

Every conversation decision lives in ai_operator.Conversation (shared with
chat_test.py). This file only handles time and audio:

  caller turn (Muse)                       Aide
  speechEnd ── plan on the latest partial, start the reply early
  final ────── plan again; keep the early reply if same_turn(), else restart
               short/trailing turn: hold until speechEnd + 0.7 s for a resumed sentence
               commit
  +0.7 s ───── no "say" text yet: play a cached filler
  +2.5 s ───── still none: fire a second identical request (hedge); first "say" wins
  +6.0 s ───── still none: cancel both, play the retry line, drop any late package
  say text ─── streams into a TTS context; audio goes to Twilio as media + marks

Marks are named "u{segment}:{chars}": the characters of that segment's text
covered by the audio before the mark. When the caller barges in we send
"clear", and the last mark Twilio echoed tells how much of Aide's line they
actually heard; only that part goes into the transcript.

The engine is only used through engine.start(ctx) -> Reply (chunks() for the
spoken line, `package` for the analysis), so a different ReplyEngine drops in
without changes here.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import random
import re
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ai_operator.brain import Package, Reply
from ai_operator.conversation import Conversation, TurnPlan
from ai_operator.prompts import ALL_FIXED, FILLERS
from ai_operator.state import CallState, Phase
from tts import SAMPLE_RATE, AudioChunk, FixedAudio, TTSProvider, Utterance, cut_to_word, duration_ms

log = logging.getLogger("call")


@dataclass
class Timing:
    filler: float = 0.7        # after speech end: play a filler if no "say" text yet
    hedge: float = 2.5         # still none: fire a second identical request
    timeout: float = 6.0       # still none: give up and play the retry line
    hold: float = 0.7          # after speech end: wait this long for a resumed sentence
    final_wait: float = 1.5    # speechEnd but no final transcript: use the last partial
    slice_ms: int = 400        # audio per Twilio media message (one mark each)
    chars_per_s: float = 15.0  # played-text estimate when audio has no alignment


# Words that alone don't interrupt Aide ("mm-hmm", "yeah" while Aide talks).
BACKCHANNEL = {"mm", "mmm", "hmm", "mhm", "mmhmm", "uh", "huh", "uhhuh", "um", "yeah", "yes", "yep",
               "ok", "okay", "right", "sure", "oh", "ah", "no", "nah", "got", "it", "true"}
_TRAILING = re.compile(r"\b(and|um|uh|so|like|but|because|or)\W*$", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower().replace("-", ""))


def substantive(text: str) -> bool:
    """Enough to count as a new turn: three or more words, or any non-backchannel word."""
    w = _words(text)
    return len(w) >= 3 or any(x not in BACKCHANNEL for x in w)


# Don't count toward the opening's three words (background chatter often starts with these).
OPENING_FILLER = {"so", "um", "uh", "like", "and", "okay", "ok"}
BARGE_SPEECH_MS = 500


def can_barge(text: str, *, opening: bool, speech_ms: float) -> bool:
    """Enough to stop Aide mid-line. The opening needs three real words; elsewhere two
    words (not all backchannel), or one real word with ~500 ms of continuous speech."""
    w = _words(text)
    if opening:
        return len([x for x in w if x not in OPENING_FILLER]) >= 3
    if len(w) >= 3:
        return True
    if not any(x not in BACKCHANNEL for x in w):
        return False
    return len(w) >= 2 or speech_ms >= BARGE_SPEECH_MS


def needs_hold(text: str) -> bool:
    """A turn that may be unfinished: under three words, or ends on "and"/"um"/"so"..."""
    return len(_words(text)) < 3 or bool(_TRAILING.search(text or ""))


_AUX = {"is", "are", "am", "was", "were", "do", "does", "did", "can", "could", "would", "will", "should",
        "shall", "have", "has", "had", "may", "might", "isn't", "aren't", "wasn't", "don't", "doesn't",
        "didn't", "can't", "couldn't", "wouldn't", "won't", "shouldn't", "haven't", "hasn't"}
_WH = {"what", "what's", "which", "who", "whom", "whose", "where", "when", "why", "how", "how's"}
_LEAD_IN = {"so", "and", "okay", "ok", "alright", "also", "just", "but", "then", "well", "now", "and"}
_TAG = re.compile(r"\b(right|correct|okay|ok|yeah)\s*\?$")
_SENTENCE_BREAK = re.compile(r"[.!?]+[\"')]*(?=\s)")


def final_question(text: str) -> tuple[int, str]:
    """(where the last sentence of `text` starts, and what it is: "yes_no", "open", or "none").

    "So that's m-a-y-a-dot-r at utexas dot edu, did I get that right?" -> yes_no
    "Got it. What year are you in?" -> open; either-or questions ("now, or later?") -> open
    """
    t = (text or "").rstrip()
    breaks = [m.end() for m in _SENTENCE_BREAK.finditer(t)]
    start = breaks[-1] if breaks else 0
    while start < len(t) and t[start].isspace():
        start += 1
    q = t[start:].lower()
    if not q.endswith("?"):
        return start, "none"
    if " or " in q:
        return start, "open"
    clauses = [[w for w in re.findall(r"[a-z']+", c)] for c in re.split(r"[,;:]", q)]
    clauses = [c[next((i for i, w in enumerate(c) if w not in _LEAD_IN), len(c)):] for c in clauses]
    clauses = [c for c in clauses if c]
    if clauses and clauses[0][0] in _WH:
        return start, "open"
    if _TAG.search(q) or any(c[0] in _AUX for c in clauses):
        return start, "yes_no"
    return start, "open"


def _ms(t: float | None, t0: float) -> int | None:
    return None if t is None else round((t - t0) * 1000)


# ---- playback ----------------------------------------------------------------------

class Segment:
    """One line of Aide's audio: a cached fixed line, a filler, or a streamed reply."""

    def __init__(self, n: int, kind: str, *, text: str = "", key: str | None = None,
                 utterance: Utterance | None = None, interruptible: bool = True,
                 end_call: bool = False, opening: bool = False) -> None:
        self.n = n
        self.opening = opening              # part of the greeting: harder to interrupt
        self.kind = kind                    # "fixed" | "filler" | "say"
        self.key = key
        self._text = text
        self.utterance = utterance
        self.interruptible = interruptible
        self.end_call = end_call
        self.played_chars = 0               # from the last mark Twilio echoed
        self.covered_chars = -1             # chars covered by all audio sent (-1: unknown)
        self.sent_ms = 0.0
        self.first_sent: float | None = None
        self.done: asyncio.Future[bool] = asyncio.get_running_loop().create_future()  # True: played fully
        self.audio: bytes | None = None     # cached audio (fixed lines, fillers)
        self.op_id = ""                     # operator_text turn_id on dashboards
        self.lat: dict[str, Any] | None = None   # latency row of the turn this line answers
        self.on_first_send: list[Callable[[Segment], None]] = []
        self.on_finish: list[Callable[[Segment, str, bool], None]] = []

    @property
    def text(self) -> str:
        return self.utterance.text if self.utterance is not None else self._text

    def played_text(self, full: bool) -> str:
        if self.utterance is not None and self.sent_ms == 0:
            return ""                       # no audio ever went out (TTS failed)
        if full:
            chars = self.covered_chars if self.covered_chars >= 0 else len(self.text)
        else:
            chars = self.played_chars
        return cut_to_word(self.text, chars)


class Player:
    """Sends Aide's audio to Twilio in order, with marks, and tracks what has played."""

    def __init__(self, send: Callable[[dict], Awaitable[None]], timing: Timing, label: str) -> None:
        self._send = send
        self.timing = timing
        self.label = label
        self.stream_sid = ""
        self._queue: asyncio.Queue[Segment] = asyncio.Queue()
        self._pending: dict[int, Segment] = {}     # queued or sent, not yet fully played
        self._counter = itertools.count(1)
        self._task: asyncio.Task | None = None
        self.on_idle: Callable[[], None] | None = None
        self.on_start: Callable[[], None] | None = None
        self.last_finished: Segment | None = None

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    @property
    def interruptible(self) -> bool:
        return all(s.interruptible for s in self._pending.values())

    @property
    def playing_crisis(self) -> bool:
        return any(s.key and s.key.startswith("crisis") for s in self._pending.values())

    def current(self) -> Segment | None:
        return self._pending[min(self._pending)] if self._pending else None

    def play(self, kind: str, *, text: str = "", key: str | None = None, audio: bytes | None = None,
             utterance: Utterance | None = None, interruptible: bool = True,
             end_call: bool = False, opening: bool = False) -> Segment:
        seg = Segment(next(self._counter), kind, text=text, key=key, utterance=utterance,
                      interruptible=interruptible, end_call=end_call, opening=opening)
        seg.audio = audio
        self._pending[seg.n] = seg
        self._queue.put_nowait(seg)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"player-{self.label}")
        if self.on_start:
            self.on_start()
        return seg

    async def _media(self, seg: Segment, audio: bytes, chars: int) -> None:
        if seg.first_sent is None:
            seg.first_sent = time.monotonic()
            for cb in seg.on_first_send:
                cb(seg)
        await self._send({"event": "media", "streamSid": self.stream_sid,
                          "media": {"payload": base64.b64encode(audio).decode()}})
        await self._send({"event": "mark", "streamSid": self.stream_sid,
                          "mark": {"name": f"u{seg.n}:{max(chars, 0)}"}})
        seg.sent_ms += duration_ms(audio)

    async def _run(self) -> None:
        step = self.timing.slice_ms * SAMPLE_RATE // 1000
        while True:
            seg = await self._queue.get()
            if seg.done.done():
                continue                       # cleared before it started
            if seg.utterance is None:
                audio = seg.audio or b""
                total = max(1, len(audio))
                for i in range(0, len(audio), step):
                    end = min(len(audio), i + step)
                    await self._media(seg, audio[i:end], round(len(seg.text) * end / total))
                seg.covered_chars = len(seg.text)
            else:
                async for chunk in seg.utterance:
                    await self._send_chunk(seg, chunk)
                if seg.utterance.error:
                    log.error("[%s] TTS failed for u%d: %s", self.label, seg.n, seg.utterance.error)
            await self._send({"event": "mark", "streamSid": self.stream_sid,
                              "mark": {"name": f"u{seg.n}:end"}})

    async def _send_chunk(self, seg: Segment, chunk: AudioChunk) -> None:
        step = self.timing.slice_ms * SAMPLE_RATE // 1000
        audio = chunk.audio
        for i in range(0, len(audio), step):
            end = min(len(audio), i + step)
            chars = chunk.chars_at(duration_ms(audio[:end]))
            if chars < 0:      # no alignment: estimate from speaking rate
                chars = min(len(seg.text), int((seg.sent_ms + duration_ms(audio[i:end]))
                                               / 1000 * self.timing.chars_per_s))
            await self._media(seg, audio[i:end], chars)
            seg.covered_chars = chars

    def on_mark(self, name: str) -> None:
        m = re.fullmatch(r"u(\d+):(\d+|end)", name or "")
        if not m:
            return
        seg = self._pending.get(int(m.group(1)))
        if seg is None:
            return                              # cleared; Twilio echoes pending marks after a clear
        if m.group(2) != "end":
            seg.played_chars = int(m.group(2))
            return
        self._finish(seg, full=True)
        if not self._pending and self.on_idle:
            self.on_idle()

    def _finish(self, seg: Segment, full: bool) -> None:
        self._pending.pop(seg.n, None)
        if seg.done.done():
            return
        if full:
            self.last_finished = seg
        played = seg.played_text(full)
        seg.done.set_result(full)
        for cb in seg.on_finish:
            cb(seg, played, full)

    def clear(self) -> list[Segment]:
        """Stop all of Aide's audio now. Returns the segments that were cut."""
        segs = [self._pending[n] for n in sorted(self._pending)]
        if self._task:
            self._task.cancel()
            self._task = None
        while not self._queue.empty():
            self._queue.get_nowait()
        for seg in segs:
            if seg.utterance is not None:
                seg.utterance.cancel()
            self._finish(seg, full=False)
        asyncio.create_task(self._send({"event": "clear", "streamSid": self.stream_sid}))
        return segs

    def finish_all(self) -> None:
        """The call is ending: record whatever part of the pending lines was heard."""
        for seg in [self._pending[n] for n in sorted(self._pending)]:
            self._finish(seg, full=False)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for seg in list(self._pending.values()):
            if seg.utterance is not None:
                seg.utterance.cancel()
            self._pending.pop(seg.n, None)
            if not seg.done.done():
                seg.done.set_result(False)


# ---- caller turns -------------------------------------------------------------------

@dataclass
class CallerTurn:
    muse_id: Any
    partial: str = ""
    t_start: float = field(default_factory=time.monotonic)
    t_end: float | None = None
    barged: bool = False                     # this turn already interrupted Aide
    spec: tuple[TurnPlan, Reply] | None = None
    final_timer: asyncio.Task | None = None
    handled: bool = False
    # Aide's line that was playing when the caller started, and how many of its
    # characters had played by then (from the last mark Twilio echoed).
    overlap: tuple[Segment, int] | None = None


@dataclass
class ActiveTurn:
    """The turn Aide is answering, from final transcript until its package lands."""
    n: int
    plan: TurnPlan | None = None
    t0: float = 0.0                          # speech end
    replies: list[Reply] = field(default_factory=list)
    winner: Reply | None = None
    utterance: Utterance | None = None
    holding: bool = False
    held_text: str = ""
    task: asyncio.Task | None = None
    lat: dict[str, Any] = field(default_factory=dict)   # appended to state.latency once logged
    logged: bool = False
    turn_count: int = 0                      # state.turn_count after this turn's commit


class CallSession:
    def __init__(self, state: CallState, *, engine, tts: TTSProvider, fixed: FixedAudio,
                 send: Callable[[dict], Awaitable[None]], hangup: Callable[[], Awaitable[None]],
                 emit: Callable[[dict], Awaitable[None]] | None = None,
                 on_change: Callable[[CallState], None] | None = None,
                 timing: Timing | None = None, label: str | None = None) -> None:
        self.state = state
        self.engine = engine
        self.tts = tts
        self.fixed = fixed
        self.timing = timing or Timing()
        self.label = label or f"call …{state.call_id[-6:]}"
        self._hangup_cb = hangup
        self._emit_cb = emit
        self._on_change_cb = on_change
        self.conv = Conversation(state, engine, on_change=self._changed)
        self.player = Player(send, self.timing, self.label)
        self.player.on_idle = self._on_idle
        self.player.on_start = self._cancel_silence

        self._turns: dict[Any, CallerTurn] = {}
        self._done_ids: set[Any] = set()     # Muse turns already answered (late events ignored)
        self._active: ActiveTurn | None = None
        self._turn_no = 0
        self._held_text = ""                 # a short turn whose sentence the caller resumed
        self._deferred: tuple[str, Segment, int] | None = None   # backchannel waiting for Aide's line to end
        self._silence: asyncio.Task | None = None
        self._caller_speaking = False
        self._tasks: set[asyncio.Task] = set()
        self._last_filler: str | None = None
        self._op_line = itertools.count(1)
        self.ended = False
        self.hung_up = False

    # ---- lifecycle ----------------------------------------------------------

    async def start(self, stream_sid: str) -> None:
        self.player.stream_sid = stream_sid
        self._spawn(self.tts.connect(), "tts-connect")
        self._spawn(self.engine.warm(), "spark-warm")
        key, extra = self.conv.opening()
        self._play_fixed(key, opening=True)
        if extra:
            u = self.tts.utterance()
            u.feed(extra)
            u.end()
            self._segment(self.player.play("say", utterance=u, opening=True))

    async def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        self._cancel_silence()
        if self._active:
            self._abandon(self._active, keep_package=False)
        for turn in self._turns.values():
            if turn.spec:
                turn.spec[1].cancel()
        self.player.finish_all()
        for t in list(self._tasks):
            t.cancel()
        self.player.stop()
        self.conv.end()
        try:
            await self.tts.close()
        except Exception as exc:
            log.warning("[%s] TTS close failed: %r", self.label, exc)
        self._log_latency_summary()

    async def _hangup(self) -> None:
        if self.hung_up or self.ended:
            return
        self.hung_up = True
        log.info("[%s] Aide is ending the call", self.label)
        await self._hangup_cb()

    def _spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=f"{name}-{self.label}")
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("[%s] task %s failed", self.label, task.get_name(), exc_info=task.exception())

    # ---- outputs ------------------------------------------------------------

    def _changed(self, state: CallState) -> None:
        if self._on_change_cb:
            self._on_change_cb(state)

    def _emit(self, msg: dict) -> None:
        if self._emit_cb:
            self._spawn(self._emit_cb({**msg, "call_id": self.state.call_id, "ts": time.time()}), "emit")

    def _record_line(self, seg: Segment, played: str, full: bool) -> None:
        """What Aide actually said goes into the transcript and to dashboards."""
        if seg.kind == "filler":
            return
        if played:
            self.conv.operator_said(played)
        if not full:
            log.info("[%s] Aide cut off after %r", self.label, played)
        self._emit({"type": "operator_text", "turn_id": seg.op_id, "text": played, "final": True})
        if seg.kind == "say" and full and seg.sent_ms == 0 and not self.hung_up:
            log.error("[%s] Aide's line produced no audio (%s); playing the retry line", self.label,
                      seg.utterance.error if seg.utterance else "?")
            self._play_fixed("retry")
        if seg.end_call and full:
            self._spawn(self._hangup(), "hangup")

    def _segment(self, seg: Segment) -> Segment:
        seg.op_id = f"a{next(self._op_line)}"
        seg.on_finish.append(self._record_line)
        return seg

    def _play_fixed(self, key: str, *, end_call: bool = False, interruptible: bool | None = None,
                    opening: bool = False) -> Segment:
        if interruptible is None:
            interruptible = not key.startswith("crisis")
        seg = self._segment(self.player.play("fixed", key=key, text=ALL_FIXED[key],
                                             audio=self.fixed.get(key), interruptible=interruptible,
                                             end_call=end_call, opening=opening))
        self._emit({"type": "operator_text", "turn_id": seg.op_id, "text": seg.text, "final": False})
        return seg

    def _play_say(self, u: Utterance, *, interruptible: bool = True) -> Segment:
        return self._segment(self.player.play("say", utterance=u, interruptible=interruptible))

    def _play_filler(self) -> Segment:
        choices = [k for k in FILLERS if k != self._last_filler]
        key = random.choice(choices)
        self._last_filler = key
        seg = self.player.play("filler", key=key, text=FILLERS[key], audio=self.fixed.get(key))
        return seg

    # ---- silence ------------------------------------------------------------

    def _on_idle(self) -> None:
        if self._deferred is not None:
            deferred, self._deferred = self._deferred, None
            if self._resolve_backchannel(*deferred):
                return
        self._arm_silence()

    # ---- backchannels ("yeah", "mm-hmm" said over Aide) ------------------------

    def _backchannel_answers(self, seg: Segment, at: int) -> tuple[bool, str]:
        """Is a backchannel that started `at` characters into `seg` an answer to it?
        Only when the line ends in a yes/no question and the caller started during
        that question. The safety question counts even in its either-or form."""
        start, kind = final_question(seg.text)
        if kind == "none":
            return False, "Aide's line wasn't a question"
        if kind == "open" and not (self.state.safety_pending and seg.kind == "say"):
            return False, "not a yes/no question"
        if at < start:
            return False, "said before Aide's final question"
        return True, ""

    def _resolve_backchannel(self, text: str, seg: Segment, at: int) -> bool:
        """Once Aide's line is over: answer the backchannel as a turn, or ignore it."""
        played = seg.done.done() and seg.done.result()
        ok, why = self._backchannel_answers(seg, at) if played else (False, "Aide's line was cut off")
        if not ok and played and not seg.opening and substantive(text):
            # Too short to interrupt Aide ("psychology"), but a real answer: take it now.
            ok = True
        if ok:
            log.info("[%s] treating %r as the answer to %r", self.label, text, seg.text[-60:])
            self._start_turn(CallerTurn(muse_id=None, t_end=time.monotonic()), text)
            return True
        log.info("[%s] ignored backchannel %r (%s)", self.label, text, why)
        self.conv.caller_present()
        return False

    def _arm_silence(self) -> None:
        self._cancel_silence()
        if self.ended or self.hung_up or self._caller_speaking or self.player.busy:
            return
        if self._active is not None and self._active.winner is None:
            return                     # still waiting on the model; the stall timers cover it
        self._silence = self._spawn(self._silence_after(self.conv.silence_timeout), "silence")

    def _cancel_silence(self) -> None:
        if self._silence and not self._silence.done() and self._silence is not asyncio.current_task():
            self._silence.cancel()
        self._silence = None

    async def _silence_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        plan = self.conv.on_silence()
        log.info("[%s] %.0f s of silence -> %s", self.label, seconds, plan.fixed)
        self._play_fixed(plan.fixed, end_call=plan.end_call)

    # ---- Muse events ----------------------------------------------------------

    def _turn(self, muse_id: Any) -> CallerTurn | None:
        if muse_id in self._done_ids:
            return None
        turn = self._turns.get(muse_id)
        if turn is None:
            turn = self._turns[muse_id] = CallerTurn(muse_id)
        return turn

    async def on_muse_event(self, kind: str, turn_id: Any) -> None:
        if self.ended:
            return
        if kind == "speechStart":
            turn = self._turn(turn_id)
            if turn is None:
                return
            cur = self.player.current()
            if cur is not None and turn.overlap is None:
                turn.overlap = (cur, cur.played_chars)
            self._caller_speaking = True
            self._cancel_silence()
            a = self._active
            if a is not None and a.holding:
                # They kept going: drop the guess and answer the whole sentence later.
                log.info("[%s] caller resumed after %r; waiting for the rest", self.label, a.plan.caller_text)
                self._held_text = a.plan.caller_text
                self._abandon(a, keep_package=False)
        elif kind == "speechEnd":
            turn = self._turn(turn_id)
            if turn is None:
                return
            turn.t_end = time.monotonic()
            self._caller_speaking = False
            self._early_start(turn)
            turn.final_timer = self._spawn(self._final_fallback(turn), "final-wait")

    async def on_transcript(self, turn_id: Any, text: str, final: bool) -> None:
        if self.ended:
            return
        turn = self._turn(turn_id)
        if turn is None:
            return                     # late partial/final for a turn already answered
        if not final:
            turn.partial = text
            self._cancel_silence()
            cur = self.player.current()
            if cur is not None:
                if can_barge(text, opening=cur.opening, speech_ms=(time.monotonic() - turn.t_start) * 1000):
                    self._interrupt(turn, f"partial {text!r}")
            elif substantive(text):
                self._interrupt(turn, f"partial {text!r}")   # spoke again before Aide answered
            return
        self._on_final(turn, (text or "").strip())

    async def _final_fallback(self, turn: CallerTurn) -> None:
        await asyncio.sleep(self.timing.final_wait)
        if not turn.handled:
            log.warning("[%s] no final transcript %.1f s after speech end; using partial %r",
                        self.label, self.timing.final_wait, turn.partial)
            self._on_final(turn, turn.partial.strip())

    def on_mark(self, name: str) -> None:
        self.player.on_mark(name)

    # ---- barge-in -------------------------------------------------------------

    def _interrupt(self, turn: CallerTurn, why: str) -> None:
        """The caller is really talking: stop Aide's audio and the turn in flight."""
        if turn.barged:
            return
        if self.player.busy:
            if not self.player.interruptible:
                return                 # crisis lines always play to the end
            turn.barged = True
            cur = self.player.current()
            if cur is not None and cur.lat is not None:
                cur.lat["barged"] = True
            heard = round(cur.played_chars / max(1, len(cur.text)) * 100) if cur else 0
            log.info("[%s] barge-in on u%s (%s, %s) after %d%% of it played (%s)", self.label,
                     cur.n if cur else "?", cur.kind if cur else "?", cur.key or "reply" if cur else "?",
                     heard, why)
            if self._active:
                self._abandon(self._active, keep_package=True)
            self.player.clear()
        elif self._active is not None and not self._active.holding:
            turn.barged = True
            log.info("[%s] caller spoke again before Aide answered (%s)", self.label, why)
            self._abandon(self._active, keep_package=True)

    def _abandon(self, a: ActiveTurn, *, keep_package: bool) -> None:
        """Stop answering turn `a`. With keep_package, its analysis of the caller's
        line still lands in the record (merged in turn order, see apply_package)."""
        if self._active is a:
            self._active = None
        if a.task and a.task is not asyncio.current_task():
            a.task.cancel()
        if a.utterance is not None:
            a.utterance.cancel()
        keep = (a.winner or (a.replies[0] if a.replies else None)) if keep_package and a.turn_count else None
        for r in a.replies:
            if r is not keep:
                r.cancel()
        if keep is not None and not keep.package.done():
            self._spawn(self._late_package(keep, a), "late-package")

    async def _late_package(self, reply: Reply, a: ActiveTurn) -> None:
        pkg = await reply.package
        if self.ended:
            return
        # Fields a newer turn already set are kept; apply_package skips them.
        result = self.conv.apply_package(pkg, a.plan)
        log.info("[%s] turn %d: package from interrupted reply applied (%s)", self.label, a.n,
                 "; ".join(result.notes) or "ok")
        if result.crisis_now:
            self._crisis_now()

    # ---- turns ----------------------------------------------------------------

    def _text_with_held(self, text: str) -> str:
        return f"{self._held_text} {text}".strip() if self._held_text else text

    def _early_start(self, turn: CallerTurn) -> None:
        if not turn.partial.strip() or (self.player.busy and not turn.barged):
            return
        if self._active is not None:
            return
        plan = self.conv.plan(self._text_with_held(turn.partial))
        if plan.needs_model:
            turn.spec = (plan, self.conv.start_reply(plan))

    def _on_final(self, turn: CallerTurn, text: str) -> None:
        if turn.handled:
            return
        turn.handled = True
        self._turns.pop(turn.muse_id, None)
        if turn.muse_id is not None:
            self._done_ids.add(turn.muse_id)
        if turn.final_timer and turn.final_timer is not asyncio.current_task():
            turn.final_timer.cancel()
        if turn.t_end is None:
            turn.t_end = time.monotonic()     # no speechEnd event: the final marks the end
            self._caller_speaking = False

        def drop_spec() -> None:
            if turn.spec:
                turn.spec[1].cancel()
                turn.spec = None

        if not text:
            drop_spec()
            if self._held_text:
                self._start_turn(turn, "")     # answer the held sentence on its own
            else:
                self._arm_silence()
            return
        if turn.overlap is None and self.player.current() is not None:
            cur = self.player.current()        # no speechStart seen: judge by now
            turn.overlap = (cur, cur.played_chars)
        speech_ms = ((turn.t_end or time.monotonic()) - turn.t_start) * 1000
        if turn.overlap is not None and not turn.barged and \
                not can_barge(text, opening=turn.overlap[0].opening, speech_ms=speech_ms):
            drop_spec()
            seg, at = turn.overlap
            if not seg.done.done():
                self._deferred = (text, seg, at)   # decided when Aide's line ends
            elif not self._resolve_backchannel(text, seg, at) and not self.player.busy:
                self._arm_silence()
            return
        if self.player.busy and not turn.barged:
            self._interrupt(turn, f"final {text!r}")
        if self._active is not None and not turn.barged and not substantive(text) \
                and not self._active.holding:
            drop_spec()
            log.info("[%s] ignored %r while Aide is answering", self.label, text)
            return
        if self._active is not None:
            self._abandon(self._active, keep_package=True)
        self._start_turn(turn, text)

    def _start_turn(self, turn: CallerTurn, text: str) -> None:
        self._deferred = None
        self._turn_no += 1
        text = self._text_with_held(text)
        self._held_text = ""
        a = ActiveTurn(n=self._turn_no, t0=turn.t_end or time.monotonic())
        a.lat = {"turn": a.n, "final_ms": _ms(time.monotonic(), a.t0)}
        self._active = a
        a.task = self._spawn(self._respond(a, turn, text), f"turn{a.n}")

    async def _respond(self, a: ActiveTurn, turn: CallerTurn, text: str) -> None:
        plan = self.conv.plan(text)
        a.plan = plan
        a.lat["move"] = plan.move
        reply = None
        if turn.spec:
            spec_plan, spec_reply = turn.spec
            turn.spec = None
            if plan.needs_model and self.conv.same_turn(spec_plan, plan):
                reply = spec_reply
                a.lat["early"] = "hit"
            else:
                spec_reply.cancel()
                a.lat["early"] = "restart"
                log.info("[%s] turn %d: early start discarded (%r -> %r)", self.label, a.n,
                         spec_plan.caller_text, plan.caller_text)
        else:
            a.lat["early"] = "none"
        if plan.needs_model and reply is None:
            reply = self.conv.start_reply(plan)
        if reply is not None:
            a.replies.append(reply)

        if plan.needs_model and needs_hold(text):
            wait = a.t0 + self.timing.hold - time.monotonic()
            if wait > 0:
                a.holding = True
                await asyncio.sleep(wait)       # a speechStart now abandons this turn
                a.holding = False

        self.conv.commit(plan)
        a.turn_count = self.state.turn_count
        if plan.fixed:
            a.lat["kind"] = plan.fixed
            self._play_turn_fixed(a, plan)
            self._active = None if self._active is a else self._active
            return
        await self._speak_reply(a, plan)

    def _play_turn_fixed(self, a: ActiveTurn, plan: TurnPlan) -> None:
        if plan.fixed == "crisis_followup" and self.player.playing_crisis:
            log.info("[%s] crisis line still playing; follow-up skipped", self.label)
            self._log_turn(a)
            return
        if plan.fixed == "crisis" and self.player.busy:
            self.player.clear()
        seg = self._play_fixed(plan.fixed, end_call=plan.end_call)
        seg.on_first_send.append(lambda s: self._first_sound(a, s))

    async def _speak_reply(self, a: ActiveTurn, plan: TurnPlan) -> None:
        t = self.timing
        gens = {}
        waits: dict[asyncio.Future, Reply] = {}

        def watch(r: Reply) -> None:
            gens[r] = r.chunks().__aiter__()
            waits[asyncio.ensure_future(gens[r].__anext__())] = r

        def hedge(why: str) -> None:
            r2 = self.conv.start_reply(plan)
            a.replies.append(r2)
            watch(r2)
            a.lat["hedged"] = why
            log.warning("[%s] turn %d: HEDGE (%s) at %d ms after speech end", self.label, a.n, why,
                        _ms(time.monotonic(), a.t0))

        watch(a.replies[0])
        first = None
        filler_played = False
        try:
            while a.winner is None:
                now = time.monotonic()
                deadlines = [a.t0 + t.timeout]
                if not filler_played:
                    deadlines.append(a.t0 + t.filler)
                if "hedged" not in a.lat:
                    deadlines.append(a.t0 + t.hedge)
                done, _ = await asyncio.wait(list(waits), timeout=max(0.0, min(deadlines) - now),
                                             return_when=asyncio.FIRST_COMPLETED)
                for fut in done:
                    r = waits.pop(fut)
                    try:
                        first = fut.result()
                    except StopAsyncIteration:
                        log.warning("[%s] turn %d: reply ended with no spoken line (%s)", self.label,
                                    a.n, r.error or "empty")
                        continue
                    a.winner = r
                    break
                if a.winner is not None:
                    break
                now = time.monotonic()
                if not waits and "hedged" not in a.lat and now < a.t0 + t.timeout:
                    hedge("first request failed")
                    continue
                if not waits or now >= a.t0 + t.timeout:
                    break
                if not filler_played and now >= a.t0 + t.filler and not self.player.busy:
                    filler = self._play_filler()
                    filler.on_first_send.append(lambda s: self._first_sound(a, s))
                    filler_played = True
                elif not filler_played and now >= a.t0 + t.filler:
                    filler_played = True        # Aide is already talking (e.g. welcome back)
                if "hedged" not in a.lat and now >= a.t0 + t.hedge:
                    hedge("no say text")
        finally:
            for fut in waits:
                fut.cancel()

        if a.winner is None:
            a.lat["timeout"] = True
            for r in a.replies:
                r.cancel()              # a late package must never update the record
            log.warning("[%s] turn %d: TIMEOUT, no say text %d ms after speech end (%d request(s)); "
                        "playing retry line", self.label, a.n, _ms(time.monotonic(), a.t0), len(a.replies))
            seg = self._play_fixed("retry")
            seg.on_first_send.append(lambda s: self._first_sound(a, s, final=True))
            if self._active is a:
                self._active = None
            return

        winner = a.winner
        a.lat["first_say_ms"] = _ms(winner.timings.get("first_say"), a.t0)
        a.lat["model_first_token_ms"] = _ms(winner.timings.get("first_token"), winner.timings.get("request", a.t0))
        if len(a.replies) > 1:
            a.lat["hedge_won"] = winner is not a.replies[0]
        for r in a.replies:
            if r is not winner:
                r.cancel()
        a.replies = [winner]

        u = self.tts.utterance()
        a.utterance = u
        seg = self._play_say(u)
        seg.lat = a.lat
        seg.on_first_send.append(lambda s: self._first_sound(a, s, final=True))
        op_id = seg.op_id
        u.feed(first)
        self._emit({"type": "operator_text", "turn_id": op_id, "text": u.text, "final": False})
        async for text in gens[winner]:
            u.feed(text)
            self._emit({"type": "operator_text", "turn_id": op_id, "text": u.text, "final": False})
        u.end()

        # Shielded: if this turn is interrupted now, the package must survive for _late_package.
        package: Package | None = await asyncio.shield(winner.package)
        if self._active is not a:
            return
        result = self.conv.apply_package(package, plan)
        if result.move:
            a.lat["move"] = f"{plan.move}->{result.move}"
        for note in result.notes:
            log.info("[%s] turn %d: %s", self.label, a.n, note)
        self._active = None
        if result.crisis_now:
            self._crisis_now()
        elif result.closing_now:
            self._play_fixed("closing", end_call=True)
        elif not self.player.busy:
            self._arm_silence()

    def _crisis_now(self) -> None:
        """The model judged it urgent: stop everything, play the crisis line."""
        if self._active:
            self._abandon(self._active, keep_package=False)
        if self.player.busy:
            self.player.clear()
        log.warning("[%s] CRISIS: playing the crisis line", self.label)
        self._play_fixed("crisis")

    # ---- latency ------------------------------------------------------------

    def _first_sound(self, a: ActiveTurn, seg: Segment, final: bool = False) -> None:
        """Called when a turn's audio first goes to Twilio. `final` = Aide's actual reply."""
        if "first_sound_ms" not in a.lat:
            a.lat["first_sound_ms"] = _ms(seg.first_sent, a.t0)
            a.lat["first_sound"] = seg.key or seg.kind
        if final or seg.kind == "fixed":
            a.lat["reply_audio_ms"] = _ms(seg.first_sent, a.t0)
            if seg.utterance is not None:
                a.lat["tts_first_audio_ms"] = _ms(seg.utterance.timings.get("first_audio"), a.t0)
            self._log_turn(a)

    def _log_turn(self, a: ActiveTurn) -> None:
        if a.logged:
            return
        a.logged = True
        lat = a.lat
        self.state.latency.append(lat)
        level = logging.WARNING if lat.get("hedged") or lat.get("timeout") else logging.INFO
        log.log(level, "[%s] turn %d latency: first sound %s ms (%s), reply audio %s ms, first say %s ms, "
                       "first TTS audio %s ms, model first token %s ms, final %s ms, early=%s, move=%s%s%s",
                self.label, a.n,
                lat.get("first_sound_ms"), lat.get("first_sound"), lat.get("reply_audio_ms"),
                lat.get("first_say_ms"), lat.get("tts_first_audio_ms"), lat.get("model_first_token_ms"),
                lat.get("final_ms"),
                lat.get("early"), lat.get("move") or "-",
                f", hedged ({lat['hedged']}" + ("" if lat.get("timeout") else
                                               f", {'hedge' if lat.get('hedge_won') else 'first'} won") + ")"
                if lat.get("hedged") else "",
                ", TIMEOUT" if lat.get("timeout") else "")

    def _log_latency_summary(self) -> None:
        rows = self.state.latency
        if not rows:
            return

        def med(key: str) -> str:
            vals = [r[key] for r in rows if r.get(key) is not None]
            return f"{statistics.median(vals):.0f} ms" if vals else "-"
        log.info("[%s] latency over %d turns: first sound median %s, reply audio median %s, "
                 "first say median %s; hedges %d, timeouts %d, barge-ins %d", self.label, len(rows),
                 med("first_sound_ms"), med("reply_audio_ms"), med("first_say_ms"),
                 sum(1 for r in rows if r.get("hedged")), sum(1 for r in rows if r.get("timeout")),
                 sum(1 for r in rows if r.get("barged")))
