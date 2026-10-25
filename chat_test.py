"""Talk to Aide in the terminal: no phone, no TTS. For tuning the prompt and flow.

    .venv/bin/python chat_test.py                 # type as the caller
    .venv/bin/python chat_test.py --script FILE   # one caller line per line of FILE
    .venv/bin/python chat_test.py --demo          # a built-in 8-turn conversation
    .venv/bin/python chat_test.py --case all      # scripted cases (call1, call2, talkative, guarded, failed_exam)
    .venv/bin/python chat_test.py --selftest      # offline checks, no API calls

Each turn prints the phase, the move, the turn goal, Aide's line (streamed as it arrives),
what changed in the record, and timings. Commands while chatting:
    /silence   act as if the caller went quiet     /record   print the full record
    /export    print the record.json export        /quit     end
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

from ai_operator import planner, safety  # noqa: E402  (env must be loaded before brain reads it)
from ai_operator.brain import (Package, Reply, SayExtractor, SparkCombinedEngine,  # noqa: E402
                               parse_package, validate_package)
from ai_operator.conversation import Conversation, TurnPlan, texts_match  # noqa: E402
from ai_operator.prompts import ALL_FIXED, FIXED_LINES  # noqa: E402
from ai_operator.state import CallState, Phase  # noqa: E402

DIM, BOLD, CYAN, YELLOW, RED, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[33m", "\033[31m", "\033[0m"
if not sys.stdout.isatty():
    DIM = BOLD = CYAN = YELLOW = RED = RESET = ""

DEMO = [
    "hey, um, I'm Maya",
    "I go to UT Austin",
    "I'm a sophomore, psychology",
    "I don't know, I've just been really lonely since I moved here, I don't have anyone to talk to",
    "pretty much every night honestly, I talk to ChatGPT about my day and how I'm feeling",
    "I think someone who's been through it, who also moved far from home",
    "yeah, that's right",
    "later is better, maybe tomorrow",
    "maya dot r",
    "yes",
]

# Scripted cases. Caller lines are grouped by the phase Aide is in, so a line is used
# when Aide is asking for that kind of thing, whatever order the questions come in.
#   "~line"  the caller kept talking before Aide could answer (on the phone: barge-in)
#   "+line"  not from the real call; added so the script can reach the end
CALL1_EXAM = ("um I had to I failed an exam and I'm going to have to drop a class it's going to put me "
              "behind on the requirements for my major I'm going to have to take an extra semester and "
              "I'm very stressed out")
CASES: dict[str, dict] = {
    "call1": {
        "about": "real call 1 (failed exam). On the phone 'So one day' no longer cuts the opening; "
                 "'they asked what's' still does.",
        "opening_cut": "Hi, this is Aide with AIEDH. I'm an AI, not a therapist",
        "identity": ["~they asked what's", "hello?", "+oh sorry, I'm Dev", "I'm not University of Texas at Dallas.",
                     "I'm a sophomore.", "I'm studying computer science"],
        "talk": [CALL1_EXAM,
                 "um all the time I talk about my relationships I talk about um what I want to eat for meals "
                 "and I just anything I I every day all the time",
                 "+I guess someone who's also fallen behind, so I don't feel like the only one"],
        "reflect": ["+yeah"],
        "timing": ["+now would be good"],
        "email": ["D A L zero six zero zero nine two", "Yes that's correct."],
        "expect": {"name_asked": True, "email": "dal060092@utdallas.edu"},
    },
    "call2": {
        "about": "real call 2 (Maya, returning). Should reach REFLECT within 5 caller turns after Welcome back.",
        "returning": {"name": "Maya"},
        "talk": ["Um I don't know I was just feeling pretty lonely um and I usually go to chatbots to talk about "
                 "it but I wanted to petter myself and use the hotline today",
                 "um I'd say I turn to chatbots about once a week when I'm randomly just you know chilling in my "
                 "bed and I feel a spur of the moment loneliness that's when I go to these chatbots",
                 "I'd say someone that's a good listener, someone that would allow me to just kind of vent about my "
                 "problems um I'd also want a high achiever student so someone that can also understand my struggle "
                 "to you know get good grades and things like that",
                 "Since the start of the semester so about a month ago.",
                 "Um I'd say it's just I kind of work around it so it's not too bad but it does exist",
                 "Um I don't know usually when things go good or bad I just go to the chatbots and I kind of look "
                 "for feedback from it",
                 "um it's about none of them I'd say probably just the responsibility that I put on myself",
                 "I enjoy playing basketball."],
        "reflect": ["exactly"],
        "timing": ["Either is fine."],
        "email": ["+m a y a dot r", "+yes"],
        "expect": {"reflect_within": 5},
    },
    "talkative": {
        "about": "shares everything in one long answer: should reach REFLECT fast.",
        "identity": ["hi, I'm Priya", "Rice", "junior, bioengineering"],
        "talk": ["honestly I've been really homesick, I moved here from Mumbai two months ago and I miss my family "
                 "so much, I don't really have friends here yet, so I end up talking to ChatGPT every night before "
                 "bed about my day, and I think what I want is someone who's been through moving countries, another "
                 "international student who gets it, someone I could maybe cook with or just get chai",
                 "yeah, mostly that",
                 "I'm not sure"],
        "reflect": ["yes, exactly"],
        "timing": ["later this week"],
        "email": ["p r i y a dot s", "yes"],
        "expect": {"reflect_within": 2},
    },
    "guarded": {
        "about": "mostly says 'I don't know': should get choices, never the same follow-up twice.",
        "identity": ["Jordan", "Texas State", "freshman, undeclared"],
        "talk": ["I don't know", "I don't know, just stuff", "idk", "maybe school I guess",
                 "I don't know, someone normal", "I guess", "not really"],
        "reflect": ["I guess", "yeah"],
        "timing": ["whenever"],
        "email": ["I'd rather not"],
        "expect": {"choices": True},
    },
    "failed_exam": {
        "about": "the call 1 caller, answering naturally.",
        "identity": ["hey, I'm Dev", "UT Dallas", "sophomore, computer science"],
        "talk": [CALL1_EXAM,
                 "honestly it's mostly my parents, they're paying for this and an extra semester is a lot of money, "
                 "and I feel like I let them down",
                 "someone who's been through failing a class, someone chill who won't judge me",
                 "I talk to ChatGPT pretty much every day, about everything really",
                 "I don't know"],
        "reflect": ["yeah, that's it"],
        "timing": ["tonight if possible"],
        "email": ["d a l zero six zero zero nine two", "yes"],
        "expect": {"email": "dal060092@utdallas.edu"},
    },
}


class CountingEngine:
    """Wraps an engine and counts model calls (to check CRISIS makes none)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    async def warm(self) -> None:
        await self.inner.warm()

    def start(self, ctx) -> Reply:
        self.calls += 1
        return self.inner.start(ctx)


def ms(reply: Reply, a: str, b: str) -> str:
    t = reply.timings
    return f"{(t[b] - t[a]) * 1000:.0f}ms" if a in t and b in t else "-"


def show_record(state: CallState, before: dict | None = None) -> None:
    rec = state.record()
    profile = {k: v for k, v in rec["profile"].items() if v is not None}
    if before is not None:
        old = {k: v for k, v in before["profile"].items() if v is not None}
        changed = {k: v for k, v in profile.items() if old.get(k) != v}
        print(f"{DIM}  learned: {json.dumps(changed, ensure_ascii=False) if changed else '-'}{RESET}")
    print(f"{DIM}  record:  {json.dumps(profile, ensure_ascii=False)}{RESET}")
    print(f"{DIM}  conf:    {json.dumps(rec['confidence'])}  crisis: {rec['crisis']['level']}"
          f"{' (' + rec['crisis']['reason'] + ')' if rec['crisis']['reason'] else ''}{RESET}")
    if before is None or before["summary"] != rec["summary"]:
        print(f"{DIM}  summary: {rec['summary']}{RESET}")


def say_fixed(conv: Conversation, key: str) -> None:
    text = ALL_FIXED[key]
    print(f"{CYAN}Aide:{RESET} {text}  {DIM}[fixed:{key}, no model call]{RESET}")
    conv.operator_said(text)


async def run_turn(conv: Conversation, engine: CountingEngine, text: str) -> bool:
    """One caller turn. Returns False when the call is over."""
    plan = conv.plan(text)
    print(f"{YELLOW}  phase={plan.phase.value}  move={plan.move or '-'}  asked={plan.asked}  "
          f"goal: {plan.goal or '-'}{RESET}")
    for note in plan.notes:
        print(f"{YELLOW}  note: {note}{RESET}")
    conv.commit(plan)
    if plan.fixed:
        say_fixed(conv, plan.fixed)
        return not plan.end_call
    return await model_turn(conv, engine, plan)


async def model_turn(conv: Conversation, engine: CountingEngine, plan: TurnPlan) -> bool:
    before = conv.state.record()
    t0 = time.monotonic()
    reply = conv.start_reply(plan)
    print(f"{CYAN}Aide:{RESET} ", end="", flush=True)
    async for chunk in reply.chunks():
        print(chunk, end="", flush=True)
    print()
    package = await reply.package
    t = reply.timings
    first_say = f"{(t['first_say'] - t0) * 1000:.0f}ms" if "first_say" in t else "-"
    print(f"{DIM}  timing:  first_token={ms(reply, 'request', 'first_token')} "
          f"first_say={first_say} say_done={ms(reply, 'request', 'say_done')} "
          f"done={ms(reply, 'request', 'done')}  model calls so far: {engine.calls}{RESET}")
    if reply.error:
        print(f"{RED}  error: {reply.error}{RESET}")
    if not reply.say.strip():
        say_fixed(conv, "retry")
        return True
    conv.operator_said(reply.say)
    result = conv.apply_package(package, plan)
    for note in result.notes:
        print(f"{YELLOW}  note: {note}{RESET}")
    if result.move:
        print(f"{YELLOW}  move: model made {result.move} (planned {plan.move}){RESET}")
    show_record(conv.state, before)
    if result.crisis_now:
        say_fixed(conv, "crisis")
    elif result.closing_now:
        say_fixed(conv, "closing")
        return False
    return True


async def chat(lines: list[str] | None) -> int:
    if not os.environ.get("MODEL_API_KEY"):
        print("MODEL_API_KEY is missing from .env", file=sys.stderr)
        return 1
    engine = CountingEngine(SparkCombinedEngine())
    await engine.warm()
    state = CallState(call_id=f"chat-{int(time.time())}", caller_phone="+10000000000")
    conv = Conversation(state, engine)

    key, extra = conv.opening()
    say_fixed(conv, key)
    if extra:
        print(f"{CYAN}Aide:{RESET} {extra}")
        conv.operator_said(extra)

    script = iter(lines) if lines is not None else None
    while True:
        if script is not None:
            text = next(script, None)
            if text is None:
                break
            print(f"\n{BOLD}You:{RESET} {text}")
        else:
            try:
                text = input(f"\n{BOLD}You:{RESET} ").strip()
            except EOFError:
                break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/record":
            print(json.dumps(state.record(), indent=2, ensure_ascii=False))
            continue
        if text == "/export":
            print(json.dumps(state.export(), indent=2, ensure_ascii=False))
            continue
        if text == "/silence":
            plan = conv.on_silence()
            say_fixed(conv, plan.fixed)
            if plan.end_call:
                break
            continue
        if not await run_turn(conv, engine, text):
            break

    conv.end()
    print(f"\n{DIM}Call over. phase_reached={state.phase_reached.value} "
          f"ready_to_match={state.ready_to_match} model calls={engine.calls}{RESET}")
    return 0


# ---- scripted cases -------------------------------------------------------------

# What Aide's last line asked for (Conversation branch) -> which script lines answer it.
QUEUE_FOR_ITEM = {"name": "identity", "school": "identity", "year_major": "identity", "year": "identity",
                  "major": "identity", "situation": "talk", "move": "talk", "reflect": "reflect",
                  "timing": "timing", "email": "email"}
QUEUE_FOR = {Phase.OPENING: "identity", Phase.IDENTITY: "identity", Phase.CONTEXT: "talk", Phase.DEEPEN: "talk",
             Phase.REFLECT: "reflect", Phase.TIMING: "timing", Phase.EMAIL: "email"}
EMAIL_ASK = "before the at sign"


async def run_case(name: str, case: dict, engine: CountingEngine) -> dict:
    from ai_operator.prompts import opening_words
    print(f"\n{BOLD}===== {name}: {case['about']}{RESET}")
    state = CallState(call_id=f"case-{name}", caller_phone="+10000000000")
    if case.get("returning"):
        state.profile.update(case["returning"])
        state.returning = True
    conv = Conversation(state, engine)
    key, extra = conv.opening()
    if case.get("opening_cut"):
        print(f"{CYAN}Aide:{RESET} {case['opening_cut']}  {DIM}[opening, cut off]{RESET}")
        conv.operator_said(case["opening_cut"])
    else:
        say_fixed(conv, key)
    if extra:
        print(f"{CYAN}Aide:{RESET} {extra}")
        conv.operator_said(extra)

    queues = {k: list(case.get(k, [])) for k in set(QUEUE_FOR.values())}
    turns, ran_out = [], []
    while len(turns) < 25:
        if state.phase in (Phase.CLOSING, Phase.CRISIS):
            break
        q = QUEUE_FOR_ITEM.get(state.branch[1]) or QUEUE_FOR.get(state.phase)
        if q is None:
            break
        line = queues[q].pop(0) if queues[q] else None
        if line is None:
            line = "I'm not sure"
            ran_out.append(q)
        no_reply, added = line.startswith("~"), line.startswith("+")
        line = line.lstrip("~+")
        print(f"\n{BOLD}You:{RESET} {line}{DIM}{'  [added]' if added else ''}"
              f"{'  [Aide is cut off before answering]' if no_reply else ''}{RESET}")
        before_phase, n_lines = state.phase, len(state.transcript)
        if no_reply:
            plan = conv.plan(line)
            conv.commit(plan)
            turns.append({"phase": before_phase, "after": state.phase, "aide": None})
            continue
        go_on = await run_turn(conv, engine, line)
        said = [l.text for l in state.transcript[n_lines:] if l.speaker == "operator"]
        turns.append({"phase": before_phase, "after": state.phase, "aide": said[-1] if said else None,
                      "fixed": bool(said) and said[-1] in ALL_FIXED.values()})
        if not go_on:
            break
    conv.end()

    # ---- report
    talk = [t for t in turns if t["phase"] in (Phase.CONTEXT, Phase.DEEPEN)]
    reflect_at = next((i + 1 for i, t in enumerate(talk) if t["after"] is Phase.REFLECT), None)
    convo = [t for t in turns if t["phase"] in (Phase.CONTEXT, Phase.DEEPEN, Phase.REFLECT, Phase.TIMING)]
    replies = [t["aide"] for t in turns if t["aide"] and not t.get("fixed")]
    openers = [opening_words(r) for r in replies]
    repeated = sorted({o for o in openers if openers.count(o) > 1})
    long_ = [r for r in replies if len(r.split()) > 25]
    no_question = [r for r in replies if "?" not in r]
    email_asks = [t for t in turns if t["aide"] and EMAIL_ASK in t["aide"].lower()]
    moves = state.moves
    checks = {
        "reached CLOSING": state.phase is Phase.CLOSING,
        "email asked exactly once, after TIMING": (len(email_asks) == 1 and email_asks[0]["after"] is Phase.EMAIL)
                                                  or (state.returning and state.has("email") and not email_asks),
        "no move twice in a row (except FOLLOW_THREAD)": all(a != b or a == "FOLLOW_THREAD"
                                                            for a, b in zip(moves, moves[1:])),
        "CHATBOT_DOORWAY at most once": moves.count("CHATBOT_DOORWAY") <= 1,
        "DEEPEN at most 4 questions": planner.deepen_moves(state) <= 4,
        "replies at most 25 words": not long_,
        "every reply asks a question": not no_question,
    }
    exp = case.get("expect", {})
    if exp.get("name_asked"):
        checks["name asked"] = state.has("name")
    if exp.get("email"):
        checks[f"email = {exp['email']}"] = state.profile["email"] == exp["email"]
    if exp.get("reflect_within"):
        checks[f"REFLECT within {exp['reflect_within']} caller turns of the first question"] = \
            reflect_at is not None and reflect_at <= exp["reflect_within"]
    if exp.get("choices"):
        checks["got OFFER_CHOICES"] = "OFFER_CHOICES" in moves
    print(f"\n{BOLD}----- {name} report{RESET}")
    print(f"  caller turns: {len(turns)} total, {len(convo)} from 'what made you call' through TIMING; "
          f"REFLECT after {reflect_at} of them")
    print(f"  moves: {' > '.join(moves) or '-'}")
    print(f"  repeated opening words: {', '.join(repr(o) for o in repeated) or 'none'}")
    for r in long_:
        print(f"  {len(r.split())} words: {r!r}")
    for r in no_question:
        print(f"  no question: {r!r}")
    if ran_out:
        print(f"  {YELLOW}script ran out of lines for: {', '.join(ran_out)} (used 'I'm not sure'){RESET}")
    print(f"  profile: {json.dumps({k: v for k, v in state.profile.items() if v}, ensure_ascii=False)}")
    print(f"  summary: {state.summary}")
    for label, ok in checks.items():
        print(f"  {'ok  ' if ok else RED + 'FAIL' + RESET} {label}")
    return {"name": name, "turns": len(turns), "convo": len(convo), "reflect_at": reflect_at, "moves": moves,
            "repeated": repeated, "failed": [k for k, v in checks.items() if not v]}


async def cases(names: list[str]) -> int:
    if not os.environ.get("MODEL_API_KEY"):
        print("MODEL_API_KEY is missing from .env", file=sys.stderr)
        return 1
    engine = CountingEngine(SparkCombinedEngine())
    await engine.warm()
    results = [await run_case(n, CASES[n], engine) for n in names]
    print(f"\n{BOLD}===== summary{RESET}")
    for r in results:
        print(f"  {r['name']:12s} turns {r['turns']:2d} (talk {r['convo']}), REFLECT after {r['reflect_at']}, "
              f"moves {'>'.join(m.replace('_', '')[:6] for m in r['moves'])}, "
              f"repeats {r['repeated'] or '-'}, {'all ok' if not r['failed'] else 'FAILED: ' + '; '.join(r['failed'])}")
    return 1 if any(r["failed"] for r in results) else 0


# ---- offline self-test ---------------------------------------------------------

class ScriptedEngine:
    """Replays canned raw model output through the real extractor and parser."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls = 0
        self.contexts = []

    async def warm(self) -> None:
        pass

    def start(self, ctx) -> Reply:
        self.calls += 1
        self.contexts.append(ctx)
        raw = self.outputs.pop(0) if self.outputs else '{"say": "Okay."}'
        reply = Reply()

        async def run() -> None:
            extractor = SayExtractor()
            for i in range(0, len(raw), 7):
                reply.push(extractor.feed(raw[i:i + 7]))
                if extractor.done:
                    reply.end_say()
            extractor.finish()
            reply.resolve(parse_package(raw)[1])
        reply.task = asyncio.create_task(run())
        return reply


def pkg_json(say: str, **learned) -> str:
    conf = learned.pop("confidence", {})
    crisis = learned.pop("crisis", {"level": "none", "reason": ""})
    summary = learned.pop("summary", None)
    return json.dumps({"say": say, "ready_for_next_phase": False, "learned": learned,
                       "confidence": conf, "summary": summary, "crisis": crisis})


async def selftest() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        failures += not cond

    print("SayExtractor")
    raw = ('{"say": "She said \\"hi\\" \\u00e9 \\ud83d\\ude00 a\\\\b\\nc", '
           '"learned": {"say": "nested"}, "x": 1}')
    want = 'She said "hi" é 😀 a\\b c'
    for size in (1, 2, 3, 5, 11, len(raw)):
        ex = SayExtractor()
        got = "".join(ex.feed(raw[i:i + size]) for i in range(0, len(raw), size))
        check(got == want and ex.done, f"escapes split across {size}-char chunks -> {got!r}")
    ex = SayExtractor()
    got = "".join(ex.feed(c) for c in '{"ready_for_next_phase": false, "learned": {"say": "no"}, "say": "yes"}')
    check(got == "yes", f"say not first, nested 'say' ignored -> {got!r}")
    ex = SayExtractor()
    got = "".join(ex.feed(c) for c in '```json\n{"say": "fenced"}\n```')
    check(got == "fenced", f"code fence -> {got!r}")
    ex = SayExtractor()
    got = ex.feed("Just plain text.") + ex.feed(" More.")
    ex.finish()
    check(ex.say == "Just plain text. More." and ex.mode == "plain", f"plain text -> {ex.say!r}")
    ex = SayExtractor()
    raw = 'Sounds tough. What hits hardest?{"say": "x", "learned": {"ai_use": "weekly"}}'
    got = "".join(ex.feed(raw[i:i + 4]) for i in range(0, len(raw), 4))
    ex.finish()
    check(got == "Sounds tough. What hits hardest?" and ex.done, f"plain text then JSON: JSON never spoken -> {got!r}")
    check(parse_package(raw)[1].learned == {"ai_use": "weekly"}, "...and its package still parses")

    print("narration guard")
    from types import SimpleNamespace as NS

    class FakeClient:
        """Streams canned replies like the OpenAI client; records each request's messages."""
        def __init__(self, replies: list[str]) -> None:
            self.replies, self.requests = list(replies), []
            self.chat = NS(completions=NS(create=self.create))

        async def create(self, **kw):
            self.requests.append(kw["messages"])
            raw = self.replies.pop(0)

            class Stream:
                def __aiter__(st):
                    async def gen():
                        for i in range(0, len(raw), 5):
                            yield NS(choices=[NS(finish_reason=None, delta=NS(content=raw[i:i + 5]))])
                    return gen()

                async def close(st):
                    pass
            return Stream()

    from ai_operator.prompts import TurnContext
    ctx = TurnContext(phase="DEEPEN", goal="g", move="FOLLOW_THREAD", known={}, confidence={}, summary=None,
                      lines=[("caller", "hi")], local_time="Friday 9:00 PM")
    client = FakeClient([pkg_json("That extra semester is weighing on you, I'll ask what part feels heaviest."),
                         pkg_json("That extra semester is a lot. What part feels heaviest?")])
    r = SparkCombinedEngine(client=client).start(ctx)
    said = "".join([c async for c in r.chunks()])
    await r.package
    check(said == "That extra semester is a lot. What part feels heaviest?", f"narration never spoken -> {said!r}")
    check(len(client.requests) == 2 and client.requests[1] == client.requests[0], "asked again, same request")
    client = FakeClient([pkg_json("So it's been lonely since you moved. I'll be honest, did I get that right?")])
    r = SparkCombinedEngine(client=client).start(ctx)
    said = "".join([c async for c in r.chunks()])
    check(len(client.requests) == 1 and said.endswith("right?"), "a reply with its question is spoken as is")

    print("validate_package")
    p = validate_package({"learned": {"themes": ["Loneliness", "bogus", "homesick"], "stage": "Weeks",
                                      "wants": "hugs", "interests": ["a", "b", "c", "d"], "name": None},
                          "confidence": {"situation": 1.4, "themes": "x"},
                          "crisis": {"level": "URGENT", "reason": "plan"}})
    check(p.learned == {"themes": ["loneliness", "homesick"], "stage": "weeks", "interests": ["a", "b", "c"]},
          f"enums/lists cleaned -> {p.learned}")
    check(p.confidence == {"situation": 1.0}, f"confidence clamped -> {p.confidence}")
    check(p.crisis_level == "urgent", "crisis level normalized")
    check(parse_package("not json")[1] is None, "invalid JSON -> no package")

    print("advance()")
    s = CallState(call_id="t1")
    s.phase = Phase.IDENTITY
    s.profile.update(name="A", school="UT Austin", year="junior")
    check(planner.advance(s) is Phase.IDENTITY, "identity waits for major")
    s.profile.update(major="math")
    check(planner.advance(s) is Phase.CONTEXT, "identity -> context without any email")
    s.phase = Phase.CONTEXT
    check(planner.advance(s) is Phase.CONTEXT, "context waits for 'what made you call'")
    s.attempts["situation"] = 1
    check(planner.advance(s) is Phase.DEEPEN, "context -> deepen once it's answered")
    s.phase = Phase.DEEPEN
    s.profile.update(situation="x", themes=["loneliness"])
    s.confidence.update(situation=0.6)
    check(planner.advance(s) is Phase.DEEPEN, "deepen waits for wants")
    s.profile.update(wants="vent")
    s.confidence.update(wants=0.6)
    check(planner.advance(s) is Phase.DEEPEN and planner.choose_move(s, "a good listener I guess") == "ASK_AI_USE",
          "ready at 0.6 but ai_use empty -> one ASK_AI_USE turn first")
    s.moves = ["CHATBOT_DOORWAY"]
    check(planner.advance(s) is Phase.REFLECT, "...unless the doorway was already used")
    s.profile.update(wants=None, partner_traits=["international student"])
    s.confidence["wants"] = 0.5
    check(planner.advance(s) is Phase.REFLECT, "partner_traits count as knowing what they want (any confidence)")
    s.confidence["wants"] = 0.6
    s.profile.update(wants="vent", partner_traits=None)
    s.moves = []
    s.profile.update(ai_use="nightly")
    check(planner.advance(s) is Phase.REFLECT, "deepen -> reflect: situation + theme + wants at 0.6")
    s.profile.update(wants=None, ai_use=None)
    s.moves = ["FOLLOW_THREAD"] * 4
    check(planner.advance(s) is Phase.REFLECT, "deepen -> reflect after 4 moves, even with gaps")
    s.phase = Phase.REFLECT
    s.reflect_confirmed = True
    check(planner.advance(s) is Phase.TIMING, "reflect -> timing when confirmed")
    s.phase = Phase.TIMING
    s.profile["timing"] = "now"
    check(planner.advance(s) is Phase.EMAIL, "timing -> email")
    s.phase = Phase.EMAIL
    s.email_done = True
    check(planner.advance(s) is Phase.CLOSING, "email -> closing")
    check(planner.timing_from_text("maybe tomorrow") == "later", "timing keyword: later")
    check(planner.spelled_letters("so that's m-a-y-a-r at utexas dot edu?") == "mayar", "spell-back parsed")
    for said, want in [("So m-a-y-a-dot-r at utexas dot edu?", "maya.r"),
                       ("that's m-a-y-a dot r at utexas dot edu", "maya.r"),
                       ("j-d-o-e-underscore-2 at rice dot edu", "jdoe_2"),
                       ("Got it, d-a-l-zero-six-zero-zero-nine-two, did I get that right?", "dal060092"),
                       ("Which school do you go to?", "")]:
        got = planner.spelled_letters(said)
        check(got == want, f"spell-back {said!r} -> {got!r}")
    check(planner.spelled_domain("d-a-l-one at utdallas dot edu, right?") == "utdallas.edu", "domain from spell-back")
    check(planner.email_domain("University of Texas at Dallas") == "utdallas.edu", "long school name -> domain")
    for said, want in [("Rice", "Rice"), ("um I go to UT Dallas.", "UT Dallas"),
                       ("I'm not University of Texas at Dallas.", "University of Texas at Dallas"),
                       ("I don't know", None), ("I'd rather tell you about my week first if that's ok", None)]:
        check(planner.school_from_text(said) == want, f"school from {said!r} -> {planner.school_from_text(said)!r}")

    print("moves")
    def mv(text: str, **kw) -> str:
        st = CallState(call_id="m")
        st.phase = Phase.DEEPEN
        for k, v in kw.items():
            if k in st.profile:
                st.profile[k] = v
            else:
                setattr(st, k, v)
        return planner.choose_move(st, text)
    check(mv("I don't know") == "CHATBOT_DOORWAY", "vague, situation unclear -> CHATBOT_DOORWAY")
    check(mv("I don't know", moves=["CHATBOT_DOORWAY"]) == "OFFER_CHOICES", "doorway at most once -> OFFER_CHOICES")
    check(mv("I don't know", moves=["ASK_AI_USE"]) == "OFFER_CHOICES", "...and not after ASK_AI_USE either")
    got = mv("idk", situation="x", moves=["OFFER_CHOICES"])
    check(mv("I don't know", situation="x", moves=["CHATBOT_DOORWAY"]) == "OFFER_CHOICES",
          "a vague answer to a new question still gets choices")
    check(got == "ASK_WANTS", f"vague twice in a row: no second follow-up, move on -> {got}")
    check(mv("I failed an exam and now I have to drop a class and take an extra semester") == "FOLLOW_THREAD",
          "rich answer -> FOLLOW_THREAD")
    got = mv("mostly my roommate", situation="x", moves=["ASK_WANTS"])
    check(got != "ASK_WANTS", f"never the same move twice in a row -> {got}")
    got = mv("my roommate never talks to me and it's been like that since I moved in", situation="x",
             moves=["FOLLOW_THREAD", "FOLLOW_THREAD"])
    check(got == "ASK_WANTS", f"two questions left and wants open -> ASK_WANTS even after a rich answer ({got})")
    long_sad = ("my grandma died last month and I couldn't go home for the funeral and everyone here just "
                "keeps going like nothing happened and I've been crying every night and I can't focus and")
    check(mv(long_sad) == "LET_THEM_TALK", "long emotional unfinished answer -> LET_THEM_TALK")
    check(mv(long_sad.rsplit(" and", 1)[0]) == "FOLLOW_THREAD", "the same answer, finished -> FOLLOW_THREAD")
    st = CallState(call_id="m2")
    st.profile.update(situation="lonely", themes=["loneliness"])
    goal = planner.move_goal(st, "THEME_PROBE")
    check("haven't found your people" in goal and "reveals" not in goal.lower()[:5], "theme probe uses the bank")
    check("ASK_AI_USE" in planner.move_goal(st, "FOLLOW_THREAD"), "early exit asks about AI use while it's empty")
    st.profile["ai_use"] = "weekly"
    check("(move WRAP)" in planner.move_goal(st, "FOLLOW_THREAD"), "...otherwise every move offers the early WRAP")
    from ai_operator.prompts import THEME_PROBES
    from ai_operator.state import THEMES
    check(set(THEME_PROBES) == set(THEMES), f"theme bank covers every theme ({len(THEME_PROBES)}/{len(THEMES)})")

    print("bug 1: a confirmed email is committed and never asked again (call 1's lines)")
    conv = Conversation(CallState(call_id="b1"), ScriptedEngine([]))
    conv.opening()
    st = conv.state
    st.profile.update(name="Dev", school="University of Texas at Dallas", year="sophomore", major="computer science",
                      timing="now")
    st.phase, st.branch = Phase.EMAIL, (None, "email")
    conv.operator_said(FIXED_LINES["email_ask"])
    p1 = conv.plan("D A L zero six zero zero nine two")
    check(p1.asked == "email" and "Spell it back" in p1.goal, "email answer -> spell-back goal")
    conv.commit(p1)
    conv.operator_said("Got it, d-a-l-zero-six-zero-zero-nine-two, did I get that right?")
    p2 = conv.plan("Yes that's correct.")
    conv.commit(p2)
    check(st.profile["email"] == "dal060092@utdallas.edu", f"email committed by code -> {st.profile['email']}")
    check(st.email_done and p2.fixed.startswith("closing"), f"then the closing ({p2.fixed})")
    st2 = CallState(call_id="b1b")
    st2.profile.update(school="Some College")
    conv2 = Conversation(st2, ScriptedEngine([]))
    st2.phase, st2.branch = Phase.EMAIL, (None, "email")
    conv2.operator_said("Got it, j-o-one, did I get that right?")
    conv2.commit(conv2.plan("yes"))
    check(st2.profile["email"] == "jo1" and st2.email_done, f"unknown domain: local part kept -> {st2.profile['email']}")

    print("email is only asked at the end")
    conv = Conversation(CallState(call_id="e1"), ScriptedEngine([]))
    conv.opening()
    st = conv.state
    goals = []
    for line, learned in [("I'm Dev", {"name": "Dev"}), ("UT Dallas", {"school": "UT Dallas"}),
                          ("sophomore, computer science", {"year": "sophomore", "major": "computer science"})]:
        p = conv.plan(line)
        conv.commit(p)
        goals.append(p.goal)
        st.profile.update(learned)
        st.last_learned = set(learned)
        conv.operator_said("Okay, next question?")
    check(not any("email" in g.lower() for g in goals), "no email in IDENTITY goals")
    check("what made them call" in goals[-1], f"year+major -> what made you call ({goals[-1][-60:]!r})")
    check("What year are you, and what are you studying?" in goals[1], "year and major in one question")
    st.phase, st.branch = Phase.TIMING, (None, "timing")
    p = conv.plan("now works")
    check(p.fixed == "email_ask" and p.phase is Phase.EMAIL, f"TIMING answered by keyword -> cached email line ({p.fixed})")

    print("bug 3: noise before the name question doesn't use up its tries")
    conv = Conversation(CallState(call_id="b3"), ScriptedEngine([]))
    conv.opening()
    conv.operator_said("Hi, this is Aide with AIEDH. I'm an AI, not a therapist")   # cut off
    for line in ["they asked what's", "hello?"]:
        p = conv.plan(line)
        conv.commit(p)                          # no reply in between: the caller spoke again
    check(conv.state.tries("name") == 0, f"name tries -> {conv.state.tries('name')}")
    check(p.goal.startswith(planner.REINTRO_GOAL), "re-introduce and ask the name")

    print("emotional answer to 'what made you call' isn't an IDENTITY skip")
    conv = Conversation(CallState(call_id="id0"), ScriptedEngine([]))
    conv.opening()
    conv.state.profile.update(name="Priya", school="Rice", year="junior", major="bio")
    conv.state.branch = (None, "situation")
    conv.operator_said("What made you call tonight?")
    p = conv.plan("I've been really homesick and lonely since I moved here from Mumbai two months ago")
    check(not p.flags.get("identity_skipped") and p.phase is Phase.DEEPEN, f"-> {p.phase.value}, {p.notes}")

    print("emotional share during IDENTITY")
    conv = Conversation(CallState(call_id="id1"), ScriptedEngine([]))
    conv.opening()
    st = conv.state
    p = conv.plan("I'm Sam and honestly I've been really lonely since I got here")
    conv.commit(p)
    check(st.identity_skipped and p.phase is Phase.DEEPEN and p.move, f"skips to a move ({p.phase.value}, {p.move})")
    st.profile.update(name="Sam")
    st.phase = Phase.EMAIL
    check(planner.next_item(st, Phase.EMAIL) == "school", "missing school is asked at the end, before the email")

    print("model wraps early")
    conv = Conversation(CallState(call_id="w1"), ScriptedEngine([]))
    st = conv.state
    st.returning = True
    st.profile.update(name="Maya", ai_use="weekly, about loneliness")
    conv.opening()
    conv.operator_said("Welcome back, Maya. What made you want to call tonight?")
    p = conv.plan("I've been lonely since the semester started and I'd love a good listener who gets grade pressure")
    conv.commit(p)
    check(p.phase is Phase.DEEPEN and p.move, f"answer -> a move ({p.move})")
    res = conv.apply_package(validate_package({"move": "WRAP", "learned": {"partner_traits": ["good listener", "gets grade pressure"]}}), p)
    check(st.phase is Phase.REFLECT and st.moves[-1] == "WRAP", f"model's WRAP recorded -> {st.phase.value} {st.moves}")
    check(st.profile["partner_traits"] == ["good listener", "gets grade pressure"], "partner_traits merged")
    res = conv.apply_package(validate_package({"move": "CHATBOT_DOORWAY"}), p)
    check(st.moves[-1] == "WRAP", "a move the goal didn't offer is ignored")
    conv.operator_said("So it's been lonely since the semester started. Did I get that right?")
    p = conv.plan("exactly")
    conv.commit(p)
    check(st.reflect_confirmed and p.phase is Phase.TIMING, f"yes -> TIMING ({p.phase.value})")

    print("sounding like a person")
    from ai_operator.prompts import dynamic_block
    conv = Conversation(CallState(call_id="h1"), ScriptedEngine([]))
    conv.opening()
    conv.operator_said("Thanks, that helps. What year are you?")
    ctx = conv.context(conv.plan("junior"))
    block = dynamic_block(ctx)
    check(ctx.no_ack and "Don't open with an acknowledgment" in block, "a recent 'thanks' -> no acknowledgment opener")
    check('"thanks that"' in block and "Your last replies:" in block, "last replies and their openings passed in")
    check("partner_traits" in CallState(call_id="x").export()["profile"], "partner_traits in the export")

    print("merge rules")
    eng = ScriptedEngine([])
    conv = Conversation(CallState(call_id="t2"), eng)
    conv.opening()
    st = conv.state
    plan = conv.plan("I'm Sam")
    conv.commit(plan)
    conv.apply_package(validate_package({"learned": {"name": "Sam", "themes": ["grief"],
                                                     "email": "sam@x.edu"},
                                         "confidence": {"situation": 0.4}}), plan)
    check(st.profile["name"] == "Sam" and st.profile["themes"] == ["grief"], "non-null values merged")
    check(st.profile["email"] is None, "unconfirmed email ignored")
    conv.apply_package(validate_package({"learned": {"name": None, "themes": ["burnout", "grief"]},
                                         "confidence": {"situation": 0}}), plan)
    check(st.profile["name"] == "Sam", "null never overwrites")
    check(st.profile["themes"] == ["burnout", "grief"], "lists replace whole")
    st.profile["situation"] = "x"
    st.confidence["situation"] = 0.6
    conv.apply_package(validate_package({"learned": {}, "confidence": {"situation": 0}}), plan)
    check(st.confidence["situation"] == 0.6, "bare 0 confidence for a known field is ignored")
    conv.apply_package(validate_package({"learned": {}, "confidence": {"situation": 0.8}}), plan)
    check(st.confidence["situation"] == 0.8, "confidence updated")
    conv.apply_package(validate_package({"learned": {"name": "Samantha"}, "summary": "Samantha is sad."}), plan)
    check(st.profile["name"] == "Samantha", "correction replaces")
    check(st.summary is None, "summary with name rejected")
    conv.apply_package(validate_package({"learned": {}, "summary": "Feels lonely."}), plan)
    check(st.summary is None, "summary only updates when something new was learned")

    print("late packages merge in turn order")
    conv = Conversation(CallState(call_id="t2c"), ScriptedEngine([]))
    conv.opening()
    st = conv.state
    p1 = conv.plan("I'm Samuel and I go to Rice")
    conv.commit(p1)
    p2 = conv.plan("actually just call me Sam, I've been feeling stuck")
    conv.commit(p2)
    conv.apply_package(validate_package({"learned": {"name": "Sam", "situation": "Feels stuck."},
                                         "confidence": {"situation": 0.8},
                                         "summary": "Feels stuck at school."}), p2)
    res = conv.apply_package(validate_package({"learned": {"name": "Samuel", "school": "Rice",
                                                           "situation": "Older take."},
                                               "confidence": {"situation": 0.3},
                                               "summary": "Older summary.",
                                               "crisis": {"level": "concern", "reason": "hopeless"}}), p1)
    check(st.profile["name"] == "Sam" and st.profile["situation"] == "Feels stuck.",
          f"turn 1's late package doesn't overwrite turn 2 -> {st.profile['name']}, {st.profile['situation']}")
    check(st.profile["school"] == "Rice", "...but still fills fields turn 2 didn't set")
    check(st.confidence["situation"] == 0.8 and st.summary == "Feels stuck at school.",
          f"confidence and summary keep turn 2's -> {st.confidence['situation']}, {st.summary!r}")
    check(st.last_learned == {"name", "situation"}, f"last_learned stays turn 2's -> {st.last_learned}")
    check(st.crisis_level == "concern", "a late package can still raise the crisis level")
    check(sum("ignored" in n for n in res.notes) == 4, f"each skip noted -> {res.notes}")
    conv.apply_package(validate_package({"learned": {"name": "Samantha"}}), p2)
    check(st.profile["name"] == "Samantha", "same-turn correction still replaces")

    print("volunteered fields")
    conv = Conversation(CallState(call_id="t2b"), ScriptedEngine([]))
    conv.opening()
    st = conv.state
    st.branch = ("school", "year_major")    # asked school; would ask year + major next
    st.profile.update(name="Maya", school="UT Austin", year="sophomore", major="psych")
    st.last_learned = {"school", "year", "major"}    # caller gave all three
    check(conv.resolve_asked() == "situation", f"skips volunteered year + major -> {conv.resolve_asked()}")

    print("tripwire")
    for text, fires in [("honestly some days I want to die", True),
                        ("I don't want to die, I just feel stuck", True),
                        ("I would never hurt myself", True),
                        ("I don't want to be here anymore", True),
                        ("this exam is killing me", False),
                        ("I'm dying to go home for break", False)]:
        got = safety.tripwire(text)
        check(bool(got) == fires, f"{text!r} -> {got!r}")

    print("crisis flow")
    eng = ScriptedEngine([pkg_json("Are you safe right now, or thinking about hurting yourself?",
                                   crisis={"level": "concern", "reason": "hopeless"})])
    conv = Conversation(CallState(call_id="t4"), eng)
    conv.opening()
    st = conv.state
    p1 = conv.plan("honestly some days I want to die")
    check(p1.safety and p1.goal == planner.SAFETY_GOAL, "tripwire -> safety question goal")
    conv.commit(p1)
    reply = conv.start_reply(p1)
    say = "".join([c async for c in reply.chunks()])
    conv.apply_package(await reply.package, p1)
    conv.operator_said(say)
    check(st.safety_pending and st.crisis_level == "concern", "safety pending, level concern")
    calls_before = eng.calls
    p2 = conv.plan("yeah")
    check(p2.fixed == "crisis" and p2.phase is Phase.CRISIS, "yes -> CRISIS, fixed crisis line")
    conv.commit(p2)
    conv.operator_said(FIXED_LINES["crisis"])
    p3 = conv.plan("what do I do")
    check(p3.fixed == "crisis_followup" and not p3.needs_model, "caller speech -> fixed follow-up")
    conv.commit(p3)
    conv.operator_said(FIXED_LINES["crisis_followup"])
    check(conv.silence_timeout == 30.0, "crisis silence timeout is 30 s")
    p4 = conv.on_silence()
    check(p4.fixed == "crisis_closing" and p4.end_call, "30 s silence -> crisis closing, end call")
    check(eng.calls == calls_before, "no model call once in CRISIS")
    exp = st.export()
    check(exp["phase_reached"] == "CRISIS" and exp["ready_to_match"] is False
          and exp["crisis"]["level"] == "urgent", "export: CRISIS, urgent, not ready_to_match")

    print("model urgent -> CRISIS; bad JSON")
    eng = ScriptedEngine([pkg_json("I hear you.", crisis={"level": "urgent", "reason": "has a plan"}),
                          "{ not json at all"])
    conv = Conversation(CallState(call_id="t5"), eng)
    conv.opening()
    p1 = conv.plan("I have a plan")
    conv.commit(p1)
    r = conv.start_reply(p1)
    _ = [c async for c in r.chunks()]
    res = conv.apply_package(await r.package, p1)
    check(res.crisis_now and conv.state.phase is Phase.CRISIS, "model urgent -> crisis_now")
    conv2 = Conversation(CallState(call_id="t6"), eng)
    conv2.opening()
    p2 = conv2.plan("hi I'm Jo")
    conv2.commit(p2)
    r2 = conv2.start_reply(p2)
    _ = [c async for c in r2.chunks()]
    res2 = conv2.apply_package(await r2.package, p2)
    check(not res2.changed and not r2.say, "invalid JSON: no update, no crash (retry line plays)")

    print("silence + early start")
    conv = Conversation(CallState(call_id="t7"), ScriptedEngine([]))
    conv.opening()
    waits, keys = [], []
    for _ in range(3):
        waits.append(conv.silence_timeout)
        keys.append(conv.on_silence().fixed)
    check(keys == ["reminder_1", "reminder_2", "closing"], f"silence sequence -> {keys}")
    check(waits == [12.0, 12.0, 15.0], f"silence waits -> {waits}")
    check(texts_match("um I just moved here", "I just moved here"), "filler-only difference matches")
    check(not texts_match("I just moved here", "I just moved here and I hate it"), "new content restarts")

    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--case", help=f"scripted case: {', '.join(CASES)}, or all")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
                        format="%(asctime)s %(levelname)-7s %(name)-6s %(message)s", datefmt="%H:%M:%S")
    if args.selftest:
        return asyncio.run(selftest())
    if args.case:
        names = list(CASES) if args.case == "all" else args.case.split(",")
        return asyncio.run(cases(names))
    lines = None
    if args.script:
        lines = [ln.strip() for ln in args.script.read_text().splitlines() if ln.strip()]
    elif args.demo:
        lines = DEMO
    return asyncio.run(chat(lines))


if __name__ == "__main__":
    raise SystemExit(main())
