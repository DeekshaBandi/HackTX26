"""Live merge: two callers who both want to talk now are joined in one Twilio conference.

  caller says "now"   -> CallSession plays merge_hold instead of closing_now and calls join()
  someone similar     -> both hear merge_intro, then both calls are redirected into one
  is waiting             <Conference>; that ends each call's media stream, so Aide is gone
  nobody in time      -> closing_now ("we'll text you"), then hang up

"Similar" is the matching API's own answer: the other caller is this caller's live_match
(closest student on a call right now) and scores at least the API's threshold. MERGE_ANY=1
in .env merges any two waiting callers (a demo fallback if the matching API is down).
The merge is reported to the matching API as a call_merge match, so the judge site sees it.

Run the offline checks:  .venv/bin/python merge.py --selftest
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from matching_client import _post, api_url

log = logging.getLogger("merge")

STILL_S = 20.0   # "Still looking, hang tight." this long into the hold
TIMEOUT_S = 10.0


def hold_seconds() -> float:
    """Read when used, since server.py loads .env after its imports."""
    return float(os.environ.get("MERGE_HOLD_S", "45"))


def merge_any() -> bool:
    return os.environ.get("MERGE_ANY", "").strip().lower() in ("1", "true", "yes")


def conference_twiml(room: str) -> str:
    # Same room as deliver.merge_calls, minus its <Say>: Aide already said the intro.
    return ("<Response><Dial><Conference startConferenceOnEnter=\"true\" endConferenceOnExit=\"true\" "
            f"beep=\"false\">{room}</Conference></Dial></Response>")


def _get(path: str) -> Any:
    with urllib.request.urlopen(api_url() + path, timeout=TIMEOUT_S) as response:
        return json.loads(response.read() or b"null")


class Waiter:
    def __init__(self, session, reporter) -> None:
        self.session = session
        self.reporter = reporter
        self.task: asyncio.Task | None = None

    @property
    def call_id(self) -> str:
        return self.session.state.call_id

    @property
    def student_id(self) -> str | None:
        return self.reporter.student_id if self.reporter is not None else None

    @property
    def gone(self) -> bool:
        return self.session.ended or self.session.hung_up


class MergeLobby:
    def __init__(self, redirect: Callable[[str, str], None],
                 broadcast: Callable[[dict], Awaitable[None]] | None = None,
                 *, hold_s: float | None = None, still_s: float = STILL_S) -> None:
        self._redirect = redirect          # blocking (Twilio SDK): (call_sid, twiml)
        self._broadcast = broadcast
        self._hold_s = hold_s
        self._still_s = still_s
        self.waiting: dict[str, Waiter] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def join(self, session, reporter) -> None:
        """The caller wants to talk now and just heard merge_hold."""
        w = Waiter(session, reporter)
        self.waiting[w.call_id] = w
        log.info("[%s] waiting for a live merge (%d waiting)", session.label, len(self.waiting))
        w.task = self._spawn(self._wait(w), f"merge-wait-{session.label}")

    def leave(self, call_id: str) -> None:
        """The call ended (hung up, or merged)."""
        w = self.waiting.pop(call_id, None)
        if w is not None:
            log.info("[%s] left the merge lobby", w.session.label)
            if w.task and w.task is not asyncio.current_task():
                w.task.cancel()

    async def _wait(self, w: Waiter) -> None:
        async with self._lock:
            found = await self._find_partner(w)
            if found is not None:
                partner, score = found
                for other in (partner, w):
                    self.waiting.pop(other.call_id, None)
                if partner.task:
                    partner.task.cancel()
                self._spawn(self._merge(partner, w, score), f"merge-{w.session.label}")
                return
        hold = self._hold_s if self._hold_s is not None else hold_seconds()
        await asyncio.sleep(min(self._still_s, hold))
        if self.waiting.get(w.call_id) is w and not w.gone and hold > self._still_s:
            w.session.play_line("merge_still")
            await asyncio.sleep(hold - self._still_s)
        if self.waiting.pop(w.call_id, None) is w and not w.gone:
            log.info("[%s] nobody to merge with after %.0f s; closing", w.session.label, hold)
            w.session.close_after_hold()

    async def _find_partner(self, w: Waiter) -> tuple[Waiter, float] | None:
        for other in list(self.waiting.values()):
            if other is w or other.gone:
                continue
            score = await self.pair_score(w, other)
            if score is not None:
                return other, score
        return None

    async def pair_score(self, a: Waiter, b: Waiter) -> float | None:
        """The pair's score if they should be merged, else None. Asks the matching API both ways."""
        best: float | None = None
        for me, other in ((a, b), (b, a)):
            if not (me.student_id and other.student_id and api_url()):
                continue
            try:
                near = await asyncio.to_thread(_get, f"/students/{me.student_id}/nearby?k=5")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("[%s] matching API nearby failed: %s", me.session.label, exc)
                continue
            live = (near or {}).get("live_match") or {}
            threshold = (near or {}).get("threshold") or 0.0
            if live.get("student_id") != other.student_id:
                log.info("[%s] live match is %s, not %s", me.session.label,
                         live.get("student_id"), other.student_id)
                continue
            score = float(live.get("score") or 0.0)
            log.info("[%s] pair score %.3f with %s (threshold %.2f)", me.session.label,
                     score, other.student_id, threshold)
            if score >= threshold:
                return score
            best = max(best or 0.0, score)
        if merge_any():
            log.info("[%s] MERGE_ANY=1: merging with %s anyway", a.session.label, b.session.label)
            return best or 0.0
        return None

    async def _merge(self, a: Waiter, b: Waiter, score: float) -> None:
        """a waited first. Both hear the intro, then both calls go into one conference."""
        room = f"aiedh_{uuid.uuid4().hex[:8]}"
        log.info("[%s] merging with %s (score %.3f) into %s", a.session.label, b.session.label, score, room)
        segs = [a.session.play_line("merge_intro"), b.session.play_line("merge_intro")]
        await asyncio.gather(*(seg.done for seg in segs), return_exceptions=True)
        if a.gone or b.gone:
            for w in (a, b):
                if not w.gone:
                    log.warning("[%s] the other caller left before the merge; closing", w.session.label)
                    w.session.close_after_hold()
            return
        a.session.merged_with, b.session.merged_with = b.call_id, a.call_id
        twiml = conference_twiml(room)
        results = await asyncio.gather(*(asyncio.to_thread(self._redirect, w.call_id, twiml) for w in (a, b)),
                                       return_exceptions=True)
        for w, result in zip((a, b), results):
            if isinstance(result, BaseException):
                log.error("[%s] Twilio redirect into %s failed: %r", w.session.label, room, result)
        log.info("[%s] merged with %s into %s", a.session.label, b.session.label, room)
        if self._broadcast:
            await self._broadcast({"type": "call_merged", "call_ids": [a.call_id, b.call_id],
                                   "room": room, "score": round(score, 3)})
        if a.student_id and b.student_id and api_url():
            body = {"student_a": a.student_id, "student_b": b.student_id, "score": score,
                    "call_id": a.call_id, "other_call_id": b.call_id, "method": "call_merge", "room": room}
            try:
                await asyncio.to_thread(_post, "/deliver", body, TIMEOUT_S)
                log.info("[%s] Matching /deliver: call_merge recorded", a.session.label)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("[%s] Matching /deliver failed: %s", a.session.label, exc)


# ---- offline checks ------------------------------------------------------------

def _selftest() -> None:
    from types import SimpleNamespace

    class FakeSession:
        def __init__(self, call_id: str) -> None:
            self.state = SimpleNamespace(call_id=call_id)
            self.label = call_id
            self.ended = self.hung_up = False
            self.lines: list[str] = []
            self.closed = False
            self.merged_with = None

        def play_line(self, key: str):
            self.lines.append(key)
            done = asyncio.get_running_loop().create_future()
            done.set_result(True)
            return SimpleNamespace(done=done)

        def close_after_hold(self) -> None:
            self.closed = True

    def reporter(sid: str):
        return SimpleNamespace(student_id=sid)

    async def run() -> None:
        os.environ["MATCHING_API_URL"] = ""   # no /deliver or /nearby during the test
        redirected: list[tuple[str, str]] = []

        def make(ok: bool) -> MergeLobby:
            lobby = MergeLobby(lambda sid, twiml: redirected.append((sid, twiml)), hold_s=0.3, still_s=0.1)

            async def pair_score(a, b):
                return 0.9 if ok else None
            lobby.pair_score = pair_score
            return lobby

        # (a) two joins -> one merge, both redirected into the same room after the intro
        lobby = make(True)
        a, b = FakeSession("CAa"), FakeSession("CAb")
        lobby.join(a, reporter("ph_a"))
        await asyncio.sleep(0.02)
        lobby.join(b, reporter("ph_b"))
        await asyncio.sleep(0.1)
        assert [sid for sid, _ in redirected] == ["CAa", "CAb"], redirected
        assert redirected[0][1] == redirected[1][1] and "<Conference" in redirected[0][1]
        assert a.lines == ["merge_intro"] and b.lines == ["merge_intro"], (a.lines, b.lines)
        assert a.merged_with == "CAb" and b.merged_with == "CAa"
        assert not lobby.waiting and not a.closed and not b.closed
        await asyncio.sleep(0.4)
        assert not a.closed and not b.closed, "a merged call must not time out"

        # (b) alone -> "still looking", then the closing
        redirected.clear()
        lobby = make(True)
        c = FakeSession("CAc")
        lobby.join(c, reporter("ph_c"))
        await asyncio.sleep(0.4)
        assert c.lines == ["merge_still"] and c.closed and not redirected and not lobby.waiting

        # (c) hung up while waiting -> never merged
        lobby = make(True)
        d, e = FakeSession("CAd"), FakeSession("CAe")
        lobby.join(d, reporter("ph_d"))
        d.ended = True
        lobby.leave("CAd")
        lobby.join(e, reporter("ph_e"))
        await asyncio.sleep(0.4)
        assert not redirected and not d.closed and e.closed

        # (d) not similar -> both keep waiting, then both close
        lobby = make(False)
        f, g = FakeSession("CAf"), FakeSession("CAg")
        lobby.join(f, reporter("ph_f"))
        lobby.join(g, reporter("ph_g"))
        await asyncio.sleep(0.05)
        assert len(lobby.waiting) == 2 and not redirected
        await asyncio.sleep(0.4)
        assert f.closed and g.closed and not redirected
        print("merge selftest: OK (4 cases)")

    asyncio.run(run())


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        logging.basicConfig(level=logging.WARNING)
        _selftest()
    else:
        print(__doc__)
