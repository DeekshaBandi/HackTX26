"""Reports each call to the matching API (matching/api.py) without holding up the voice.

  call connects        -> POST /call/start   {call_id, phone, student_id}
  profile changes      -> POST /call/turn    {student_id, call_id, package}
  call ends            -> POST /call/record  the end-of-call record, minus the transcript

student_id is "ph_" + the caller number's last 10 digits, so the live call, the
end-of-call record, and the phone app all land on the same student.

Requests for a call run one at a time, in order, on a background task. Only the
newest /call/turn waits in line (older ones are dropped), and the package is the
merged record, so a late package from an older turn never undoes a newer answer.
A failed request is logged and dropped: the call never waits on matching.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("matching")

# The first /call/start loads the embedding model, so it gets longer.
START_TIMEOUT_S = 30.0
TIMEOUT_S = 15.0
PACKAGE_KEYS = ("profile", "confidence", "summary", "crisis")


def student_id_for(phone: str) -> str | None:
    digits = "".join(ch for ch in phone or "" if ch.isdigit())
    return f"ph_{digits[-10:]}" if digits else None


def api_url() -> str:
    """Read when used, since server.py loads .env after its imports. Empty turns reporting off."""
    return os.environ.get("MATCHING_API_URL", "http://127.0.0.1:8001").strip().rstrip("/")


def _post(path: str, body: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(api_url() + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


class MatchingReporter:
    def __init__(self, call_id: str, phone: str, label: str) -> None:
        self.call_id = call_id
        self.phone = phone
        self.label = label
        self.student_id = student_id_for(phone)
        self._queue: list[tuple[str, dict[str, Any] | None]] = []
        self._wake = asyncio.Event()
        self._last_package: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._warned = False

    @property
    def enabled(self) -> bool:
        return bool(api_url())

    def start(self) -> None:
        if not self.enabled:
            return
        self._queue.append(("start", None))
        self._task = asyncio.get_running_loop().create_task(self._run(), name=f"matching-{self.label}")

    def update(self, record: dict[str, Any]) -> None:
        """The call's record changed. Queues a /call/turn if the profile part did."""
        if not self._task:
            return
        package = {key: record.get(key) for key in PACKAGE_KEYS}
        if package == self._last_package:
            return
        self._last_package = package
        self._queue = [item for item in self._queue if item[0] != "turn"]
        self._queue.append(("turn", package))
        self._wake.set()

    def finish(self, record: dict[str, Any]) -> None:
        """The call is over: send the end-of-call record, then stop."""
        if not self._task:
            return
        self._queue = [item for item in self._queue if item[0] != "turn"]
        self._queue.append(("record", {k: v for k, v in record.items() if k != "transcript"}))
        self._wake.set()

    async def _run(self) -> None:
        while True:
            while not self._queue:
                self._wake.clear()
                await self._wake.wait()
            kind, body = self._queue.pop(0)
            await self._send(kind, body)
            if kind == "record":
                return

    async def _send(self, kind: str, body: dict[str, Any] | None) -> None:
        if kind == "start":
            path, timeout = "/call/start", START_TIMEOUT_S
            payload = {"call_id": self.call_id, "phone": self.phone or None, "student_id": self.student_id}
        elif kind == "turn":
            path, timeout = "/call/turn", TIMEOUT_S
            # The API reads the profile from "learned", like a Muse Spark package.
            package = {"learned": body["profile"], "confidence": body["confidence"],
                       "summary": body["summary"], "crisis": body["crisis"]}
            payload = {"student_id": self.student_id, "call_id": self.call_id, "package": package}
        else:
            path, timeout, payload = "/call/record", TIMEOUT_S, body
        if kind == "turn" and not self.student_id:
            return  # /call/start never answered, so there is no student to update
        try:
            result = await asyncio.to_thread(_post, path, payload, timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if not self._warned:
                log.warning("[%s] Matching API %s failed (%s); is matching/api.py running at %s?",
                            self.label, path, exc, api_url())
                self._warned = True
            else:
                log.debug("[%s] Matching API %s failed: %r", self.label, path, exc)
            return
        if kind == "start" and isinstance(result, dict) and result.get("student_id"):
            self.student_id = result["student_id"]
        best = result.get("best") if isinstance(result, dict) else None
        if best:
            log.info("[%s] Matching %s: best %s (%.2f)", self.label, path,
                     best.get("student_id"), best.get("score") or 0)
        else:
            log.info("[%s] Matching %s: ok", self.label, path)
