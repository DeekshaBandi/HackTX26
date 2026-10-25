"""Offline checks of server.py's dashboard token (no Twilio, Muse, Spark or ElevenLabs calls).

    .venv/bin/python server_test.py --selftest

Runs the real app in-process on a spare port, with the Twilio webhook update and
the TTS cache build stubbed out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
import urllib.error
import urllib.request

import uvicorn
import websockets

import server

TOKEN = "test-token-123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http(url: str, data: bytes | None = None) -> tuple[int, str]:
    def go() -> tuple[int, str]:
        try:
            with urllib.request.urlopen(url, data=data, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
    return await asyncio.to_thread(go)     # blocking I/O must not stall the server's loop


async def _ws_status(url: str) -> str:
    """'open:<first message type>' or 'refused:<HTTP status>'."""
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            try:
                first = json.loads(await asyncio.wait_for(ws.recv(), 1))
                return f"open:{first.get('type')}"
            except asyncio.TimeoutError:
                return "open:"
    except websockets.exceptions.InvalidStatus as exc:
        return f"refused:{exc.response.status_code}"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def _serve(token: str):
    async def no_cache() -> dict:
        return {}
    server.configure_twilio_webhook = lambda: None
    server.fixed_audio.build = no_cache
    server.DASHBOARD_TOKEN = token
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.02)
    return srv, task, port


async def selftest() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        failures += not cond

    cap = _Capture()
    logging.getLogger("server").addHandler(cap)

    print("with DASHBOARD_TOKEN set")
    srv, task, port = await _serve(TOKEN)
    base, wsbase = f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}"
    check(any(f"/?token={TOKEN}" in l for l in cap.lines), "startup prints the dashboard link with the token")
    check((await _http(f"{base}/"))[0] == 403, "page without token -> 403")
    check((await _http(f"{base}/?token=wrong"))[0] == 403, "page with wrong token -> 403")
    status, body = await _http(f"{base}/?token={TOKEN}")
    check(status == 200 and "TOKEN_QUERY" in body, "page with token -> 200")
    check((await _http(f"{base}/calls/CAnope/record.json"))[0] == 403, "record.json without token -> 403")
    check((await _http(f"{base}/calls/CAnope/record.json?token={TOKEN}"))[0] == 404,
          "record.json with token -> passes auth (404: no such call)")
    check(await _ws_status(f"{wsbase}/dashboard") == "refused:403", "dashboard WebSocket without token -> refused")
    check(await _ws_status(f"{wsbase}/dashboard?token=wrong") == "refused:403", "...with wrong token -> refused")
    check(await _ws_status(f"{wsbase}/dashboard?token={TOKEN}") == "open:snapshot", "...with token -> snapshot")
    status, body = await _http(f"{base}/twilio/voice", data=b"CallSid=CAtest&From=%2B15550001111")
    check(status == 200 and "<Connect>" in body, "/twilio/voice stays open (no token)")
    check((await _ws_status(f"{wsbase}/twilio-audio")).startswith("open"), "/twilio-audio stays open (no token)")
    srv.should_exit = True
    await task

    print("without DASHBOARD_TOKEN")
    cap.lines.clear()
    srv, task, port = await _serve("")
    check(any("DASHBOARD_TOKEN is not set" in l for l in cap.lines), "loud warning at startup")
    check((await _http(f"http://127.0.0.1:{port}/"))[0] == 200, "page open (no protection)")
    srv.should_exit = True
    await task

    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    logging.basicConfig(level=logging.ERROR, stream=sys.stdout)
    logging.getLogger("server").setLevel(logging.INFO)
    logging.getLogger("server").propagate = False
    return asyncio.run(selftest())


if __name__ == "__main__":
    raise SystemExit(main())
