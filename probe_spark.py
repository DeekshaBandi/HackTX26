"""One-off probe of Muse Spark for the Aide operator (build step 1).

Spark mode (default), against the live Meta Model API:
  1. Streaming chat completions work through the OpenAI SDK.
  2. Whether the streamed JSON starts with "say".
  3. Latency: request -> first chunk, first content, first "say" text, done.
  Default is the config Aide uses (minimal effort, JSON described in the prompt).
  --all also runs "low" and strict json_schema (which emits keys alphabetically,
  so "say" is not first).

Endpoint mode (--endpoint), against any OpenAI-compatible server, e.g. a model
on a lab PC. Measures time to first token for a plain-text spoken line (what the
fast "say" model would do in a split design). Reads from env / .env:
  PROBE_BASE_URL            e.g. http://192.168.1.20:8080/v1   (required)
  PROBE_MODEL               model name                          (required)
  PROBE_API_KEY             optional, defaults to "none"
  PROBE_REASONING_EFFORT    optional; sent only when set
  --combined sends the full combined JSON prompt instead of the plain-text one.

    .venv/bin/python probe_spark.py                 # 3 runs, Aide's config
    .venv/bin/python probe_spark.py --all --runs 5
    .venv/bin/python probe_spark.py --endpoint --runs 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

BASE_URL = "https://api.meta.ai/v1"
MODEL = os.environ.get("SPARK_MODEL", "muse-spark-1.3")

THEMES = ["homesick", "loneliness", "breakup", "academic_pressure", "family_conflict",
          "friendship_issues", "roommate_issues", "identity", "first_gen", "financial_stress",
          "burnout", "social_anxiety", "grief", "career_uncertainty", "health"]


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


STR = {"type": "string"}
CONF = {"type": "number"}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "say": STR,
        "ready_for_next_phase": {"type": "boolean"},
        "learned": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": nullable(STR), "school": nullable(STR), "year": nullable(STR),
                "major": nullable(STR), "email": nullable(STR), "situation": nullable(STR),
                "themes": nullable({"type": "array", "items": {"type": "string", "enum": THEMES}}),
                "stage": nullable({"type": "string", "enum": ["days", "weeks", "months", "years"]}),
                "wants": nullable({"type": "string",
                                   "enum": ["vent", "advice", "been_through_it", "company"]}),
                "ai_use": nullable(STR),
                "interests": nullable({"type": "array", "items": STR}),
                "timing": nullable({"type": "string", "enum": ["now", "later", "either"]}),
            },
        },
        "confidence": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"situation": CONF, "themes": CONF, "stage": CONF, "wants": CONF},
        },
        "summary": nullable(STR),
        "crisis": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "level": {"type": "string", "enum": ["none", "concern", "urgent"]},
                "reason": STR,
            },
        },
    },
}


def require_all(schema: dict) -> dict:
    """Strict mode: every object lists all of its keys in `required`."""
    if schema.get("type") == "object":
        schema["required"] = list(schema["properties"])
        for sub in schema["properties"].values():
            require_all(sub)
    for sub in schema.get("anyOf", []):
        require_all(sub)
    return schema


require_all(SCHEMA)

# Roughly the size and shape of the real operator prompt.
SYSTEM = f"""You are Aide, the operator for AIEDH, a 24/7 phone line for university students who have been
leaning on AI chatbots for emotional support. You are on a live phone call. Your only job is to understand
the caller well enough to connect them with another real student going through something similar, then
step aside. You are a bridge, not a companion and not a therapist.

Voice: warm, plain, calm. No slang, hype, or over-the-top validation. Never give advice or therapy.
Speak 1-2 short sentences, at most 30 words, with at most one question. Briefly reflect what they said,
then ask. Spoken words only: no lists, symbols, or markdown. Never repeat the caller word for word.
Follow this turn's goal. Never ask for something already in the record.

Reply with exactly one JSON object and nothing else. The first key must be "say".
{{"say": "...", "ready_for_next_phase": false,
 "learned": {{"name": null, "school": null, "year": null, "major": null, "email": null,
   "situation": null, "themes": null, "stage": null, "wants": null, "ai_use": null,
   "interests": null, "timing": null}},
 "confidence": {{"situation": 0, "themes": 0, "stage": 0, "wants": 0}},
 "summary": null, "crisis": {{"level": "none", "reason": ""}}}}
In "learned", fill only what you learned this turn; everything else is null.
themes uses only: {", ".join(THEMES)}. stage: days|weeks|months|years.
wants: vent|advice|been_through_it|company. timing: now|later|either.
summary never includes name, school, year, major, or email."""

DYNAMIC = """Phase: CONTEXT
Goal: Find out what made them call tonight.
Record: {"name": "Jordan", "school": "UT Austin", "year": "sophomore", "major": "biology"}
Local time: Friday 11:42 PM"""

TRANSCRIPT = [
    ("assistant", "Thanks, Jordan. So what made you want to call tonight?"),
    ("user", "um I don't know, I just moved here from Ohio and I don't really have anyone to talk to "
             "so I've been talking to ChatGPT like every night"),
]


SAY_ONLY_SYSTEM = """You are Aide, the operator for AIEDH, a phone line for university students who have been
leaning on AI chatbots for emotional support. You are on a live phone call. Warm, plain, calm. Reply with only
the words you will speak next: 1-2 short sentences, at most 30 words, at most one question. Briefly reflect
what they said, then ask. No lists, symbols, markdown, or JSON."""


def messages(system: str = SYSTEM) -> list[dict]:
    msgs = [{"role": "system", "content": system},
            {"role": "system", "content": DYNAMIC}]
    msgs += [{"role": r, "content": t} for r, t in TRANSCRIPT]
    return msgs


SAY_START = re.compile(r'"say"\s*:\s*"')


async def run_once(client: AsyncOpenAI, effort: str | None, use_schema: bool, *,
                   model: str | None = None, system: str = SYSTEM, cache_key: bool = True) -> dict:
    kwargs: dict = {
        "model": model or MODEL,
        "messages": messages(system),
        "stream": True,
        "max_completion_tokens": 1500,
    }
    if effort:
        kwargs["reasoning_effort"] = effort
    if cache_key:
        kwargs["prompt_cache_key"] = "aide-probe"
    if use_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "aide_turn", "schema": SCHEMA, "strict": True},
        }

    t0 = time.perf_counter()
    first_chunk = first_content = first_say = None
    text = ""
    usage = None
    finish = None
    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        now = time.perf_counter()
        if first_chunk is None:
            first_chunk = now
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        finish = choice.finish_reason or finish
        delta = choice.delta.content or ""
        if delta:
            if first_content is None:
                first_content = now
            text += delta
            if first_say is None:
                m = SAY_START.search(text)
                # First character of the say value has arrived.
                if m and len(text) > m.end():
                    first_say = now
    done = time.perf_counter()

    ms = lambda t: None if t is None else round((t - t0) * 1000)  # noqa: E731
    stripped = re.sub(r"\s+", "", text)
    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass
    return {
        "first_chunk": ms(first_chunk), "first_content": ms(first_content),
        "first_say": ms(first_say), "done": ms(done),
        "say_first": stripped.startswith('{"say"'),
        "valid_json": parsed is not None,
        "say": parsed.get("say") if isinstance(parsed, dict) else None,
        "finish": finish, "usage": usage, "text": text,
    }


def fmt_usage(usage) -> str:
    if usage is None:
        return "usage: n/a"
    parts = [f"in={usage.prompt_tokens}", f"out={usage.completion_tokens}"]
    ptd = getattr(usage, "prompt_tokens_details", None)
    if ptd is not None and getattr(ptd, "cached_tokens", None) is not None:
        parts.append(f"cached={ptd.cached_tokens}")
    ctd = getattr(usage, "completion_tokens_details", None)
    if ctd is not None and getattr(ctd, "reasoning_tokens", None) is not None:
        parts.append(f"reasoning={ctd.reasoning_tokens}")
    return "usage: " + " ".join(parts)


async def endpoint_mode(runs: int, combined: bool) -> int:
    base_url = os.environ.get("PROBE_BASE_URL", "").strip()
    model = os.environ.get("PROBE_MODEL", "").strip()
    if not base_url or not model:
        print("Set PROBE_BASE_URL and PROBE_MODEL (in env or .env) for --endpoint", file=sys.stderr)
        return 1
    effort = os.environ.get("PROBE_REASONING_EFFORT", "").strip() or None
    client = AsyncOpenAI(base_url=base_url, api_key=os.environ.get("PROBE_API_KEY", "") or "none")
    print(f"Endpoint {base_url}  model={model}  effort={effort or '(not sent)'}  "
          f"prompt={'combined JSON' if combined else 'plain-text say'}")
    try:
        await client.models.list()
    except Exception as exc:
        print(f"(warm-up models.list failed: {exc!r})")

    rows = []
    for i in range(runs):
        try:
            r = await run_once(client, effort, False, model=model,
                               system=SYSTEM if combined else SAY_ONLY_SYSTEM, cache_key=False)
        except Exception as exc:
            print(f"  run {i + 1}: ERROR {exc!r}")
            continue
        rows.append(r)
        spoken = r["say"] if combined else r["text"].strip()
        print(f"  run {i + 1}: first_chunk={r['first_chunk']}ms first_content={r['first_content']}ms "
              f"done={r['done']}ms finish={r['finish']}  {fmt_usage(r['usage'])}")
        print(f"         said: {spoken!r}")
    firsts = [r["first_content"] for r in rows if r["first_content"] is not None]
    if firsts:
        print(f"\nmedian first_content={statistics.median(firsts)}ms  "
              f"min={min(firsts)}ms  max={max(firsts)}ms  ({len(firsts)}/{runs} runs with content)")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--all", action="store_true", help="also try effort=low and json_schema")
    parser.add_argument("--endpoint", action="store_true", help="probe PROBE_BASE_URL instead of Spark")
    parser.add_argument("--combined", action="store_true", help="--endpoint: use the combined JSON prompt")
    args = parser.parse_args()
    if args.endpoint:
        return await endpoint_mode(args.runs, args.combined)

    api_key = os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        print("MODEL_API_KEY is missing from .env", file=sys.stderr)
        return 1
    client = AsyncOpenAI(base_url=BASE_URL, api_key=api_key)

    # Warm the connection (TLS + HTTP pool) so the first measured run is fair.
    try:
        await client.models.list()
    except Exception as exc:
        print(f"(warm-up models.list failed: {exc!r})")

    configs = [("minimal", False)]
    if args.all:
        configs += [("low", False), ("minimal", True), ("low", True)]
    summary = []
    rows: list[dict] = []
    for effort, use_schema in configs:
        label = f"effort={effort:<7} schema={'json_schema' if use_schema else 'prompt-only'}"
        print(f"\n=== {label} ===")
        rows = []
        for i in range(args.runs):
            try:
                r = await run_once(client, effort, use_schema)
            except Exception as exc:
                print(f"  run {i + 1}: ERROR {exc!r}")
                continue
            rows.append(r)
            print(f"  run {i + 1}: first_chunk={r['first_chunk']}ms first_content={r['first_content']}ms "
                  f"first_say={r['first_say']}ms done={r['done']}ms say_first={r['say_first']} "
                  f"valid_json={r['valid_json']} finish={r['finish']}  {fmt_usage(r['usage'])}")
            print(f"         say: {r['say']!r}")
            if not r["valid_json"]:
                print(f"         raw: {r['text'][:300]!r}")
        if rows:
            fs = [r["first_say"] for r in rows if r["first_say"] is not None]
            summary.append((label, statistics.median(fs) if fs else None,
                            statistics.median(r["done"] for r in rows),
                            sum(r["say_first"] for r in rows), len(rows)))

    if rows:
        print("\nLast raw output:\n" + rows[-1]["text"])
    print("\n=== Summary (median ms) ===")
    for label, fs, done, say_first, n in summary:
        print(f"  {label}  first_say={fs}  done={done}  say_first={say_first}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
