"""Test call_session.py without a phone.

    .venv/bin/python call_test.py --selftest   # offline: fake Twilio, Muse, TTS and model, timers 10x faster
    .venv/bin/python call_test.py --live       # real Spark + ElevenLabs, simulated caller and Twilio playback

The fake Twilio "plays" audio in real time (or faster), echoes each mark when
the audio before it has played, and on "clear" echoes pending marks at once,
like the real one. --live prints the same per-turn latency log a phone call does.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

from ai_operator import conversation  # noqa: E402
from ai_operator.brain import Reply, SayExtractor, SparkCombinedEngine, parse_package  # noqa: E402
from ai_operator.prompts import ALL_FIXED, FIXED_LINES  # noqa: E402
from ai_operator.state import CallState, Phase  # noqa: E402
from call_session import CallSession, Timing, can_barge, final_question  # noqa: E402
from tts import SAMPLE_RATE, AudioChunk, ElevenLabsTTS, FixedAudio  # noqa: E402

LOUD = 0x10   # a non-silent mu-law byte


class FakeTwilio:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self.session: CallSession | None = None
        self.sent: list[dict] = []
        self.play_until = 0.0
        self.handles: dict[str, asyncio.TimerHandle] = {}
        self.hung_up = False

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        if msg["event"] == "media":
            n = len(base64.b64decode(msg["media"]["payload"]))
            self.play_until = max(now, self.play_until) + n / SAMPLE_RATE / self.speed
        elif msg["event"] == "mark":
            name = msg["mark"]["name"]
            delay = max(0.0, self.play_until - now)
            self.handles[name] = loop.call_later(delay, self._echo, name)
        elif msg["event"] == "clear":
            for name, h in list(self.handles.items()):
                h.cancel()
                self._echo(name)
            self.play_until = now

    def _echo(self, name: str) -> None:
        self.handles.pop(name, None)
        if self.session:
            self.session.on_mark(name)

    async def hangup(self) -> None:
        self.hung_up = True

    def count(self, event: str) -> int:
        return sum(1 for m in self.sent if m["event"] == event)


class FakeUtterance:
    def __init__(self, n: int, delay: float) -> None:
        self.id = f"u{n}"
        self.text = ""
        self.timings: dict[str, float] = {}
        self.error = None
        self.delay = delay
        self.cancelled = False
        self._q: asyncio.Queue = asyncio.Queue()
        self._ended = False

    def feed(self, text: str) -> None:
        self.text += text

    def end(self) -> None:
        if not self._ended:
            self._ended = True
            asyncio.get_running_loop().call_later(self.delay, self._produce)

    def _produce(self) -> None:
        if self.cancelled:
            return
        # 15 characters per second of audio, alignment evenly spread.
        n = len(self.text)
        audio = bytes([LOUD]) * int(n / 15 * SAMPLE_RATE)
        ms = len(audio) * 1000 / SAMPLE_RATE
        self._q.put_nowait(AudioChunk(audio, n, 0, [int(i * ms / max(n, 1)) for i in range(n)]))
        self._q.put_nowait(None)

    def cancel(self) -> None:
        self.cancelled = True
        self._q.put_nowait(None)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        while (c := await self._q.get()) is not None:
            yield c


class FakeTTS:
    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.n = 0
        self.utterances: list[FakeUtterance] = []

    async def connect(self) -> None:
        pass

    def utterance(self) -> FakeUtterance:
        self.n += 1
        u = FakeUtterance(self.n, self.delay)
        self.utterances.append(u)
        return u

    async def synthesize(self, text: str) -> bytes:
        return bytes([LOUD]) * int(len(text) / 15 * SAMPLE_RATE)

    async def close(self) -> None:
        pass


class FakeEngine:
    """Each start() takes the next script entry: (seconds before "say" streams, raw JSON
    [, extra seconds before the package lands]), or ("stall", seconds until it gives up)."""

    def __init__(self, script: list[tuple]) -> None:
        self.script = list(script)
        self.calls = 0
        self.cancelled = 0
        self.contexts = []

    async def warm(self) -> None:
        pass

    def start(self, ctx) -> Reply:
        self.calls += 1
        self.contexts.append(ctx)
        entry = self.script.pop(0) if self.script else (0.01, pkg("Okay. What else?"))
        reply = Reply()

        async def run() -> None:
            reply.stamp("request")
            package = None
            try:
                if entry[0] == "stall":
                    await asyncio.sleep(entry[1])
                    return
                await asyncio.sleep(entry[0])
                reply.stamp("first_token")
                ex = SayExtractor()
                raw = entry[1]
                for i in range(0, len(raw), 6):
                    reply.push(ex.feed(raw[i:i + 6]))
                    if ex.done:
                        reply.end_say()
                    await asyncio.sleep(0.001)
                if len(entry) > 2:
                    await asyncio.sleep(entry[2])
                package = parse_package(raw)[1]
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
            finally:
                reply.resolve(package)
        reply.task = asyncio.create_task(run())
        return reply


def pkg(say: str, **learned) -> str:
    crisis = learned.pop("crisis", {"level": "none", "reason": ""})
    return json.dumps({"say": say, "ready_for_next_phase": False, "learned": learned,
                       "confidence": {}, "summary": None, "crisis": crisis})


def fixed_audio() -> FixedAudio:
    fixed = FixedAudio(ALL_FIXED, voice_id="test")
    fixed.audio = {k: bytes([LOUD]) * int(len(v) / 15 * SAMPLE_RATE) for k, v in ALL_FIXED.items()}
    return fixed


FAST = Timing(filler=0.07, hedge=0.25, timeout=0.6, hold=0.07, final_wait=0.15)


async def make(script: list[tuple], *, speed: float = 10.0, timing: Timing = FAST, state: CallState | None = None):
    twilio = FakeTwilio(speed)
    engine = FakeEngine(script)
    tts = FakeTTS()
    state = state or CallState(call_id="CAtest" + str(time.monotonic_ns())[-6:], caller_phone="+15550001111")
    session = CallSession(state, engine=engine, tts=tts, fixed=fixed_audio(), send=twilio.send,
                          hangup=twilio.hangup, timing=timing)
    twilio.session = session
    await session.start("MZtest")
    return session, twilio, engine, tts


async def idle(session: CallSession, timeout: float = 5.0) -> None:
    """Wait until Aide has finished talking and nothing is in flight."""
    t0 = time.monotonic()
    while session.player.busy or session._active is not None:
        if time.monotonic() - t0 > timeout:
            raise TimeoutError("session never went idle")
        await asyncio.sleep(0.01)


_ids = iter(range(1000))


async def caller(session: CallSession, text: str, *, final_text: str | None = None,
                 final_delay: float = 0.02, gap: float = 0.004) -> int:
    """Muse events for one caller turn: speechStart, cumulative partials, speechEnd, final."""
    tid = next(_ids)
    await session.on_muse_event("speechStart", tid)
    words = text.split()
    for i in range(len(words)):
        await session.on_transcript(tid, " ".join(words[:i + 1]), False)
        await asyncio.sleep(gap)
    await session.on_muse_event("speechEnd", tid)
    await asyncio.sleep(final_delay)
    await session.on_transcript(tid, final_text if final_text is not None else text, True)
    return tid


def operator_lines(state: CallState) -> list[str]:
    return [l.text for l in state.transcript if l.speaker == "operator"]


# ---- offline self-test -------------------------------------------------------------

async def selftest() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        failures += not cond

    print("opening")
    s, tw, eng, tts = await make([])
    await idle(s)
    check(operator_lines(s.state) == [FIXED_LINES["opening"]], "opening played fully and recorded")
    check(tw.count("media") > 0 and all(m.get("streamSid") == "MZtest" for m in tw.sent), "media carry streamSid")
    await s.end()

    print("fast reply: early start, no filler")
    s, tw, eng, tts = await make([(0.01, pkg("Nice to meet you, Maya. Which school do you go to?", name="Maya"))])
    await idle(s)
    await caller(s, "hi I'm Maya nice to meet you")
    await idle(s)
    lat = s.state.latency[-1]
    check(lat["early"] == "hit" and eng.calls == 1, f"early start reused, 1 model call -> {lat['early']}, {eng.calls}")
    check(lat["first_sound"] == "say", f"no filler when say arrives first -> {lat['first_sound']}")
    check(s.state.profile["name"] == "Maya", "package applied")
    check(operator_lines(s.state)[-1].startswith("Nice to meet you, Maya."), "reply recorded")
    await s.end()

    print("slow reply: filler first")
    s, tw, eng, tts = await make([(0.15, pkg("Got it. And what year are you in?", school="UT Austin"))])
    await idle(s)
    await caller(s, "I go to UT Austin")
    await idle(s)
    lat = s.state.latency[-1]
    check(lat["first_sound"].startswith("filler_"), f"filler played first -> {lat['first_sound']}")
    check(60 <= lat["first_sound_ms"] <= 110, f"filler at ~70 ms (0.7 s scaled) -> {lat['first_sound_ms']}")
    check(lat["reply_audio_ms"] > lat["first_sound_ms"], "reply audio after the filler")
    check(not any(l.startswith(("Mm", "Yeah.", "Got it.", "Okay.")) and len(l) < 12
                  for l in operator_lines(s.state)), "filler not in the transcript")
    await s.end()

    print("fillers never repeat back to back")
    s, tw, eng, tts = await make([])
    keys = [s._play_filler().key for _ in range(40)]
    check(all(a != b for a, b in zip(keys, keys[1:])) and len(set(keys)) >= 6, f"{len(set(keys))} distinct, no repeats")
    s.player.stop()
    await s.end()

    print("hedge at 2.5 s")
    s, tw, eng, tts = await make([("stall", 5.0), (0.02, pkg("Okay. What year are you in?", school="Rice"))])
    await idle(s)
    await caller(s, "I go to Rice University")
    await idle(s)
    lat = s.state.latency[-1]
    check(lat.get("hedged") == "no say text" and lat.get("hedge_won") is True, f"hedge fired and won -> {lat}")
    check(eng.calls == 2 and eng.cancelled >= 1, f"2 requests, slow one cancelled -> {eng.calls}, {eng.cancelled}")
    check(s.state.profile["school"] == "Rice", "winner's package applied")
    await s.end()

    print("timeout at 6 s: retry line, late package dropped")
    s, tw, eng, tts = await make([("stall", 5.0), ("stall", 5.0)])
    await idle(s)
    t0 = time.monotonic()
    await caller(s, "my name is Zed and I'm a junior")
    await idle(s)
    lat = s.state.latency[-1]
    check(lat.get("timeout") is True, "timeout logged")
    check(operator_lines(s.state)[-1] == FIXED_LINES["retry"], "retry line played")
    check(0.55 <= time.monotonic() - t0 <= 1.2, f"after ~0.6 s scaled -> {time.monotonic() - t0:.2f}s")
    check(eng.cancelled == 2 and s.state.profile["name"] is None, "both requests cancelled, record unchanged")
    await s.end()

    print("early start restart")
    s, tw, eng, tts = await make([(0.01, pkg("Wrong guess.")), (0.01, pkg("That sounds hard. How long has it been?"))])
    await idle(s)
    await caller(s, "I just moved here", final_text="I just moved here and I hate it")
    await idle(s)
    check(s.state.latency[-1]["early"] == "restart" and eng.calls == 2, "partial != final -> restarted")
    check(operator_lines(s.state)[-1].startswith("That sounds hard"), "answered the final text")
    await s.end()

    print("barge-in")
    long_say = ("That sounds really lonely, especially being so far from home and not knowing anyone yet. "
                "How long has it felt like this?")
    s, tw, eng, tts = await make([(0.01, pkg(long_say, situation="moved far from home, lonely"))])
    await idle(s)
    await caller(s, "I moved here and I feel alone")
    while not tts.utterances or s.player.current() is None or s.player.current().played_chars < 20:
        await asyncio.sleep(0.005)
    clears = tw.count("clear")
    await caller(s, "wait actually it's more than that")
    await asyncio.sleep(0.02)
    cut = [l for l in operator_lines(s.state) if l.startswith("That sounds")]
    check(tw.count("clear") == clears + 1, "clear sent")
    check(len(cut) == 1 and 0 < len(cut[0]) < len(long_say), f"transcript cut to what played -> {cut[0]!r}")
    check(s.state.profile["situation"] is not None, "interrupted reply's package still applied")
    check(s.state.latency[0].get("barged") is True, "barge-in logged")
    await idle(s)
    await s.end()

    print("late package from an interrupted turn merges in turn order")
    s, tw, eng, tts = await make([(0.01, pkg(long_say, name="Samuel", school="Rice"), 0.6),
                                  (0.01, pkg("Got it, Sam. What year are you in?", name="Sam"))])
    await idle(s)
    await caller(s, "I'm Samuel and I go to Rice")
    while s.player.current() is None or s.player.current().kind != "say" or s.player.current().played_chars < 20:
        await asyncio.sleep(0.005)
    await caller(s, "actually just call me Sam")
    await idle(s)
    check(s.state.profile["name"] == "Sam", "turn 2 applied first")
    await asyncio.sleep(0.7)                    # turn 1's package lands now
    check(s.state.profile["name"] == "Sam", f"turn 1's late name doesn't overwrite turn 2 -> {s.state.profile['name']}")
    check(s.state.profile["school"] == "Rice", "turn 1's late package still fills school")
    await s.end()

    print("final_question()")
    for text, want in [("So that's m-a-y-a-dot-r at utexas dot edu, did I get that right?", "yes_no"),
                       ("That sounds hard. Did I get that right?", "yes_no"),
                       ("So it's maya r at rice dot edu, right?", "yes_no"),
                       ("Got it, UT Austin. What year are you in?", "open"),
                       ("What feels right?", "open"),
                       ("Would right now work, or would later be better?", "open"),
                       (FIXED_LINES["reminder_2"], "yes_no"),
                       ("Take your time. I'm still here.", "none")]:
        check(final_question(text)[1] == want, f"{want:6s} {text!r}")

    print("backchannel while Aide talks")
    s, tw, eng, tts = await make([(0.01, pkg("So that's m-a-y-a-dot-r at utexas dot edu, is that right?")),
                                  (0.01, pkg("Thanks. What made you call tonight?"))])
    s.state.profile.update(name="Maya", school="UT Austin", year="sophomore", major="psych")
    s.state.phase, s.state.branch = Phase.EMAIL, (None, "email")   # the opening's question stands in
    await idle(s)
    await caller(s, "it's maya dot r")
    while s.player.current() is None or s.player.current().kind != "say":
        await asyncio.sleep(0.005)
    clears = tw.count("clear")
    await caller(s, "yeah")
    check(tw.count("clear") == clears, "'yeah' over Aide's line doesn't interrupt")
    await idle(s)
    check(s.state.profile["email"] == "maya.r@utexas.edu" and operator_lines(s.state)[-1] == FIXED_LINES["closing"],
          f"...but answers the yes/no question once it ends (email={s.state.profile['email']}, then the closing)")
    await s.end()

    print("backchannel over an open question is ignored")
    s, tw, eng, tts = await make([(0.01, pkg("Got it, UT Austin. What year are you in?", school="UT Austin"))])
    await idle(s)
    await caller(s, "I go to UT Austin")
    while s.player.current() is None or s.player.current().kind != "say" or s.player.current().played_chars < 20:
        await asyncio.sleep(0.005)
    await caller(s, "yeah")
    await idle(s)
    callers = [l.text for l in s.state.transcript if l.speaker == "caller"]
    check(eng.calls == 1 and callers == ["I go to UT Austin"], f"no turn, no model call -> {callers}")
    check(s._silence is not None, "waiting for the real answer (silence timer running)")
    await s.end()

    print("backchannel before the final yes/no question is ignored")
    two = "That sounds really lonely, especially being so far from home and not knowing anyone. Did I get that right?"
    q_start = final_question(two)[0]
    s, tw, eng, tts = await make([(0.01, pkg(two)), (0.01, pkg("Okay. When would you like to talk?"))])
    await idle(s)
    await caller(s, "I moved here and I feel alone")
    while s.player.current() is None or s.player.current().kind != "say":
        await asyncio.sleep(0.005)
    await caller(s, "mm-hmm")                   # during the first sentence
    await idle(s)
    check(eng.calls == 1, f"'mm-hmm' during the first sentence is not an answer (calls={eng.calls})")
    s2, tw2, eng2, tts2 = await make([(0.01, pkg(two)), (0.01, pkg("Okay. When would you like to talk?"))])
    await idle(s2)
    await caller(s2, "I moved here and I feel alone")
    while s2.player.current() is None or s2.player.current().kind != "say" \
            or s2.player.current().played_chars < q_start:
        await asyncio.sleep(0.005)
    await caller(s2, "yeah")                    # during "Did I get that right?"
    await idle(s2)
    check(eng2.calls == 2, f"'yeah' during the final question is answered (calls={eng2.calls})")
    await s.end()
    await s2.end()

    print("hold + resumed sentence")
    s, tw, eng, tts = await make([(0.01, pkg("Guess")), (0.01, pkg("Moving is a lot. How long ago was it?"))],
                                 timing=Timing(filler=0.07, hedge=0.25, timeout=0.6, hold=0.15, final_wait=0.3))
    await idle(s)
    await caller(s, "um so")
    await asyncio.sleep(0.05)
    await caller(s, "I moved here from Ohio")
    await idle(s)
    callers = [l.text for l in s.state.transcript if l.speaker == "caller"]
    check(callers == ["um so I moved here from Ohio"], f"merged into one caller line -> {callers}")
    check(operator_lines(s.state)[-1].startswith("Moving is a lot"), "answered the whole sentence")
    await s.end()

    print("silence: reminders, closing, hangup")
    check(conversation.SILENCE_STEPS == (12.0, 12.0, 15.0), "real steps: 12 s, +12 s, +15 s")
    steps = conversation.SILENCE_STEPS
    conversation.SILENCE_STEPS = (0.12, 0.12, 0.15)
    s, tw, eng, tts = await make([])
    t0 = time.monotonic()
    while not tw.hung_up and time.monotonic() - t0 < 5:
        await asyncio.sleep(0.01)
    lines = operator_lines(s.state)
    check(lines[1:] == [FIXED_LINES["reminder_1"], FIXED_LINES["reminder_2"], FIXED_LINES["closing"]],
          f"reminder 1, reminder 2, closing -> {[l[:20] for l in lines[1:]]}")
    check(tw.hung_up and s.state.phase is Phase.CLOSING, "hung up after the closing played")
    await s.end()

    print("silence: 'yeah' after 'Are you still there?' is an answer (bug 4)")
    s, tw, eng, tts = await make([(0.01, pkg("Okay. If we found you someone tonight, what would make them right?"))])
    await idle(s)
    while not any(l == FIXED_LINES["reminder_2"] for l in operator_lines(s.state)):
        await asyncio.sleep(0.01)
    await caller(s, "yeah")
    await idle(s)
    check(eng.calls == 1 and s.state.phase is not Phase.CLOSING and s.state.silence_count == 0,
          f"'yeah' answered, no closing (calls={eng.calls}, phase={s.state.phase.value})")
    await s.end()
    conversation.SILENCE_STEPS = steps

    print("opening: background speech doesn't cut it (bug 2), and the name is still asked (bug 3)")
    s, tw, eng, tts = await make([(0.01, pkg("Sorry, I'm Aide, an AI that finds you another student. What's your first name?"))])
    await asyncio.sleep(0.05)
    await caller(s, "So one day")              # two real words: not enough during the opening
    await asyncio.sleep(0.02)
    check(tw.count("clear") == 0, "'So one day' doesn't interrupt the opening")
    await caller(s, "they asked what's")        # three real words: it does
    await asyncio.sleep(0.02)
    check(tw.count("clear") == 1, "'they asked what's' does")
    await idle(s)
    callers = [l.text for l in s.state.transcript if l.speaker == "caller"]
    check(callers == ["they asked what's"], f"'So one day' was dropped, not a turn -> {callers}")
    goal = eng.contexts[-1].goal if eng.contexts else ""
    check(goal.startswith("They may not have heard your introduction"), f"re-introduce + ask name -> {goal[:50]!r}")
    check(s.state.tries("name") == 0, "the unheard name question used no tries")
    await s.end()

    print("barge-in thresholds after the opening")
    check(can_barge("wait no", opening=False, speech_ms=100), "two real words interrupt")
    check(not can_barge("psychology", opening=False, speech_ms=300), "one word under 500 ms doesn't")
    check(can_barge("psychology", opening=False, speech_ms=600), "one word over 500 ms does")
    check(not can_barge("yeah okay", opening=False, speech_ms=900), "backchannel words never do")
    check(not can_barge("so um like I", opening=True, speech_ms=900), "opening: fillers don't count")
    s, tw, eng, tts = await make([(0.01, pkg(long_say)), (0.01, pkg("Psychology, nice. What made you call tonight?"))])
    await idle(s)
    await caller(s, "I moved here and I feel alone")
    while s.player.current() is None or s.player.current().kind != "say":
        await asyncio.sleep(0.005)
    clears = tw.count("clear")
    await caller(s, "psychology")               # one quick word over Aide's line
    check(tw.count("clear") == clears, "didn't interrupt")
    await idle(s)
    check(eng.calls == 2, f"...but answered once Aide's line ended (calls={eng.calls})")
    await s.end()

    print("hangup during a line: what played is still recorded")
    s, tw, eng, tts = await make([])
    while s.player.current() is None or s.player.current().played_chars < 30:
        await asyncio.sleep(0.005)
    await s.end()
    lines = operator_lines(s.state)
    check(len(lines) == 1 and 0 < len(lines[0]) < len(FIXED_LINES["opening"]), f"partial line kept -> {lines}")

    print("crisis")
    s, tw, eng, tts = await make([(0.01, pkg("Are you safe right now, or thinking about hurting yourself?",
                                             crisis={"level": "concern", "reason": "hopeless"}))])
    await idle(s)
    await caller(s, "honestly some days I want to die")
    await idle(s)
    calls = eng.calls
    await caller(s, "yeah")
    await asyncio.sleep(0.05)
    check(s.state.phase is Phase.CRISIS and s.player.playing_crisis, "yes -> CRISIS, crisis line playing")
    clears = tw.count("clear")
    await caller(s, "what do you mean, I just wanted to talk to someone")
    await asyncio.sleep(0.02)
    check(tw.count("clear") == clears, "caller can't cut off the crisis line")
    await idle(s)
    lines = operator_lines(s.state)
    check(FIXED_LINES["crisis"] in lines, "crisis line recorded in full")
    check(lines.count(FIXED_LINES["crisis_followup"]) == 0, "no follow-up stacked over the crisis line")
    await caller(s, "okay but what do I do")
    await idle(s)
    check(operator_lines(s.state)[-1] == FIXED_LINES["crisis_followup"], "later speech -> follow-up line")
    check(eng.calls == calls, "no model call in CRISIS")
    await s.end()

    print("model urgent -> crisis line cuts in")
    s, tw, eng, tts = await make([(0.01, pkg("I hear you.", crisis={"level": "urgent", "reason": "has a plan"}))])
    await idle(s)
    await caller(s, "I have a plan for how to do it")
    await idle(s)
    check(s.state.phase is Phase.CRISIS and FIXED_LINES["crisis"] in operator_lines(s.state), "crisis line played")
    await s.end()

    print("late Muse events for an answered turn are ignored")
    s, tw, eng, tts = await make([(0.01, pkg("Okay. What year are you in?"))])
    await idle(s)
    tid = await caller(s, "I go to Baylor University")
    await idle(s)
    await s.on_transcript(tid, "I go to Baylor University", False)
    await s.on_transcript(tid, "I go to Baylor University", True)
    await asyncio.sleep(0.05)
    check(eng.calls == 1 and s._active is None, "no second turn from a late final")
    await s.end()

    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


# ---- live: real Spark + ElevenLabs, simulated caller -----------------------------------

LIVE_LINES = [
    "hey, um, I'm Maya",
    "I go to UT Austin, I'm a sophomore",
    "psychology",
    "maya dot r",
    "yes",
    "I don't know, I've just been really lonely since I moved here, I don't have anyone to talk to",
    "pretty much every night honestly, I talk to ChatGPT about my day and how I'm feeling",
    "I guess since the start of the semester, so like two months",
]


async def live(lines: list[str]) -> int:
    from tts import FixedAudio
    fixed = FixedAudio(ALL_FIXED)
    print("cache:", await fixed.build())
    twilio = FakeTwilio(speed=1.0)
    state = CallState(call_id=f"CAlive{int(time.time())}", caller_phone="+15550001111")
    session = CallSession(state, engine=SparkCombinedEngine(), tts=ElevenLabsTTS(label="live"), fixed=fixed,
                          send=twilio.send, hangup=twilio.hangup)
    twilio.session = session
    await session.start("MZlive")
    seen = 0

    def show() -> None:
        nonlocal seen
        for line in state.transcript[seen:]:
            who = "Aide" if line.speaker == "operator" else "You "
            print(f"  {who}: {line.text}")
        seen = len(state.transcript)

    for text in lines:
        await idle(session, timeout=60)
        await asyncio.sleep(0.4)
        show()
        # A caller speaks at ~3 words/s; partials arrive as they go.
        await caller(session, text, final_delay=0.15, gap=0.3)
        if twilio.hung_up:
            break
    await idle(session, timeout=60)
    show()
    await session.end()
    print("\nper-turn latency (ms after the caller stopped talking):")
    for r in state.latency:
        print(f"  turn {r['turn']}: first sound {r.get('first_sound_ms')} ({r.get('first_sound')}), "
              f"reply audio {r.get('reply_audio_ms')}, first say {r.get('first_say_ms')}, "
              f"early={r.get('early')}{', hedged' if r.get('hedged') else ''}{', TIMEOUT' if r.get('timeout') else ''}")
    print("record:", json.dumps({k: v for k, v in state.profile.items() if v}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING" if args.selftest else "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(name)-6s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    if args.live:
        return asyncio.run(live(LIVE_LINES))
    return asyncio.run(selftest())


if __name__ == "__main__":
    raise SystemExit(main())
