"""Transcribe the laptop microphone with Muse -- no phone, no Twilio, no server.

Confirms that MODEL_API_KEY and Muse Voice Transcribe work on their own, using
the same MuseStream client the phone server uses.

    .venv/bin/python test_muse_mic.py      # speak, then Ctrl-C to stop

Partials print in place; each finished sentence prints on its own line.
On macOS, allow your terminal app microphone access the first time
(System Settings > Privacy & Security > Microphone).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
from pathlib import Path

import sounddevice as sd
from dotenv import load_dotenv

from muse_client import MuseStream

SAMPLE_RATE = 16000
CHUNK_MS = 80

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)-6s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)


async def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")
    api_key = os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        print("MODEL_API_KEY is missing from .env", file=sys.stderr)
        return 1

    width = max(40, shutil.get_terminal_size((100, 24)).columns - 1)

    async def on_transcript(turn_id, text: str, final: bool) -> None:
        if final:
            print(f"\r{' ' * width}\r[turn {turn_id}] {text}", flush=True)
        else:
            print(f"\r{text[-width:]:<{width}}", end="", flush=True)

    muse = MuseStream(api_key, on_transcript, label="mic", encoding="PCM_16KHZ", mode="ENDPOINTING")
    muse.start()

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop.set)

    def on_audio(indata, _frames, _time, status) -> None:  # runs on PortAudio's thread
        if status:
            print(status, file=sys.stderr)
        loop.call_soon_threadsafe(muse.send_audio, bytes(indata))

    try:
        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=int(SAMPLE_RATE * CHUNK_MS / 1000), callback=on_audio,
        )
    except Exception as exc:
        print(f"Could not open the microphone: {exc}", file=sys.stderr)
        muse.finish()
        await muse.wait_closed()
        return 1

    with stream:
        print(f"Listening on '{sd.query_devices(kind='input')['name']}'. "
              "Speak, then press Ctrl-C to finish.", file=sys.stderr)
        stop_task = asyncio.create_task(stop.wait())
        closed_task = asyncio.create_task(muse.wait_closed())
        await asyncio.wait({stop_task, closed_task}, return_when=asyncio.FIRST_COMPLETED)
        stop_task.cancel()

    print(file=sys.stderr)
    muse.finish()          # endStream, then wait for the server to flush and close
    await muse.wait_closed()
    return 0 if muse.close_code == 1000 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
