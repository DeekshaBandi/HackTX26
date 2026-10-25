"""Phase transitions, the turn goal, and (in CONTEXT/DEEPEN) the move. Code
decides; the model's ready_for_next_phase is only a hint.

One-turn lag: the caller's latest line is analyzed by the same model call that
answers it, so when a goal is chosen the record doesn't reflect that line yet.

IDENTITY and TIMING goals therefore say what to do if the latest line answered
the last question (acknowledge, ask the next thing) and what to do if it was
vague (a concrete follow-up); the branch the model took is read back on the
next turn from what it learned (see Conversation.resolve_asked).

In CONTEXT/DEEPEN the move is picked from the record plus a quick read of the
latest line itself (length, "I don't know", emotional words), which has no lag.
Every move's goal also lets the model wrap up early if the latest line already
covers what's missing; the model reports the move it actually made.
"""

from __future__ import annotations

import dataclasses
import re

from .prompts import EMAIL_LINE, THEME_PROBES, TIMING_LINE
from .safety import is_no, is_yes, tripwire
from .state import CallState, Phase

MAX_ATTEMPTS = 2           # asks per field, then move on
MAX_EMAIL_TURNS = 4        # asks + spell-backs, so a correction loop can't stall the end of the call
DEEPEN_MAX_MOVES = 4       # Aide's questions in DEEPEN; the answer to the last one goes to REFLECT
CONFIDENT = 0.6

IDENTITY_ORDER = ["name", "school", "year_major"]
IDENTITY_ITEMS = {"name", "school", "year_major", "year", "major"}
DEFERRED_IDENTITY = ["name", "school", "year_major"]   # asked before the email if IDENTITY was skipped
# Items that stand for more than one profile field.
ITEM_FIELDS = {"year_major": ("year", "major")}

# ---- moves -------------------------------------------------------------------

FOLLOW_THREAD = "FOLLOW_THREAD"
OFFER_CHOICES = "OFFER_CHOICES"
CHATBOT_DOORWAY = "CHATBOT_DOORWAY"
THEME_PROBE = "THEME_PROBE"
ASK_WANTS = "ASK_WANTS"
LET_THEM_TALK = "LET_THEM_TALK"
ASK_AI_USE = "ASK_AI_USE"
WRAP = "WRAP"
MOVES = [FOLLOW_THREAD, OFFER_CHOICES, CHATBOT_DOORWAY, THEME_PROBE, ASK_WANTS, LET_THEM_TALK,
         ASK_AI_USE, WRAP]
ONCE_PER_CALL = {CHATBOT_DOORWAY, ASK_AI_USE}

ASK = {
    "name": "Ask for their first name.",
    "school": "Ask which school they go to.",
    "year_major": 'Ask in one question what year they are and what they study, like "What year are you, and what are you studying?"',
    "year": "Ask what year they're in.",
    "major": "Ask what they're studying.",
    "email": f'Ask exactly this: "{EMAIL_LINE}"',
    "situation": "Ask what made them call tonight.",
    "timing": f'Ask exactly this: "{TIMING_LINE}"',
}

# A second try at a field: a concrete follow-up, never the same question again.
CONCRETE = {
    "name": "Ask again, simply, what first name they'd like to go by.",
    "school": "Ask simply which university they're at.",
    "year_major": "Ask simply: are they a freshman, sophomore, junior, senior, or in grad school, and what do they study?",
    "year": "Ask simply: are they a freshman, sophomore, junior, senior, or in grad school?",
    "major": "Ask simply what they're studying, or planning to.",
    "situation": "Offer a concrete either-or, like whether it's more school stuff or people stuff.",
    "timing": 'Ask simply, as a yes or no: "Do you want to talk to someone right now?"',
}

LABEL = {
    "name": "their name", "school": "their school", "year_major": "their year and major",
    "year": "their year", "major": "their major", "email": "their email",
    "situation": "what they're going through", "timing": "when they'd like to talk",
}

REFLECT_GOAL = ("Reflect back, in one or two flowing sentences, what they're going through and what "
                "they'd want from another student, using their own specific words, not a list of items, "
                "like: \"So it's been lonely since you moved, and you'd like someone who's also far from "
                "home. Did I get that right?\"")
REFLECT_AGAIN_GOAL = ("They corrected or added to your reflection. Reflect the corrected version in one "
                      "or two sentences, in their words, and ask if that's right now.")
SAFETY_GOAL = ("Calmly and directly ask whether they are safe right now, or thinking about "
               "hurting themselves. Nothing else this turn.")
PRE_CLOSING_GOAL = "Acknowledge their answer in a few words. Don't say goodbye yet."
REINTRO_GOAL = ("They may not have heard your introduction. In one short line, say you're Aide, an AI "
                "that finds them another student to talk to, and ask their first name.")

# School -> email domain, for spelling back the full address. Unknown schools let
# the model infer the domain.
SCHOOL_DOMAINS = {
    "ut austin": "utexas.edu", "university of texas at austin": "utexas.edu", "texas at austin": "utexas.edu",
    "utexas": "utexas.edu",
    "texas a&m": "tamu.edu", "texas a and m": "tamu.edu", "tamu": "tamu.edu",
    "ut dallas": "utdallas.edu", "texas at dallas": "utdallas.edu", "utd": "utdallas.edu",
    "rice": "rice.edu",
    "university of houston": "uh.edu", "texas tech": "ttu.edu", "baylor": "baylor.edu",
    "smu": "smu.edu", "southern methodist": "smu.edu", "tcu": "tcu.edu", "texas christian": "tcu.edu",
    "texas state": "txstate.edu", "unt": "unt.edu", "north texas": "unt.edu",
    "ut arlington": "uta.edu", "texas at arlington": "uta.edu", "uta": "uta.edu",
    "utsa": "utsa.edu", "ut san antonio": "utsa.edu", "texas at san antonio": "utsa.edu",
}

_DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_SYMBOLS = {"dot": ".", "period": ".", "underscore": "_", "dash": "-", "hyphen": "-", **_DIGIT_WORDS}
_TOKEN = r"(?:dot|period|underscore|dash|hyphen|zero|one|two|three|four|five|six|seven|eight|nine|[a-z0-9])"
_SPELLED = re.compile(rf"\b{_TOKEN}(?:\s*-\s*{_TOKEN}){{2,}}\b", re.IGNORECASE)
_SPACED_SYMBOL = re.compile(r"\s*-?\s+(dot|period|underscore)\s+-?\s*", re.IGNORECASE)
_SPOKEN_DOMAIN = re.compile(r"\bat\s+([a-z0-9]+(?:\s+dot\s+[a-z0-9]+)*)\s+dot\s+(edu|com|org|net)\b",
                            re.IGNORECASE)


def spelled_letters(text: str) -> str:
    """'m-a-y-a-dot-r at utexas dot edu' -> 'maya.r' (empty if there is no spell-back)."""
    text = _SPACED_SYMBOL.sub(r"-\1-", text or "")
    m = _SPELLED.search(text)
    if not m:
        return ""
    tokens = [t.lower() for t in re.split(r"\s*-\s*", m.group(0)) if t]
    return "".join(_SYMBOLS.get(t, t) for t in tokens)


def spelled_domain(text: str) -> str:
    """'...at utdallas dot edu, did I get that right?' -> 'utdallas.edu' (empty if none said)."""
    m = _SPOKEN_DOMAIN.search(text or "")
    if not m:
        return ""
    return ".".join(re.split(r"\s+dot\s+", m.group(1).lower()) + [m.group(2).lower()])


def email_domain(school: str | None) -> str | None:
    if not school:
        return None
    s = school.lower()
    for key, domain in SCHOOL_DOMAINS.items():
        if re.search(rf"\b{re.escape(key)}\b", s):
            return domain
    return None


# ---- reading the caller's latest line ------------------------------------------

_WORD = re.compile(r"[a-z0-9']+")
_FILLER = {"um", "uh", "like", "so", "and", "yeah", "mm", "hmm", "oh", "okay", "ok", "just", "i", "you",
           "know", "kind", "of", "kinda"}
_UNSURE = re.compile(r"\b(i don'?t know|idk|dunno|not sure|i guess|no idea|nothing really|"
                     r"not really|it'?s fine|whatever|i can'?t really say|hard to say)\b", re.I)
_EMOTIONAL = re.compile(
    r"\b(lonely|alone|stress(ed|ful)?|anxious|anxiety|depress\w*|sad|cry(ing)?|cried|hard time|rough|"
    r"struggl\w*|overwhelm\w*|scared|afraid|hurt\w*|hate|miss(ing)?|homesick|broke up|break ?up|"
    r"died|passed away|panic\w*|can'?t (take|handle|cope)|fail(ed|ing)|worthless|exhausted|"
    r"burn(ed|t) out|let (them|everyone|my \w+) down|ashamed|embarrass\w*|isolated|heartbroken)\b", re.I)
_TRAILING = re.compile(r"\b(and|but|so|because|like|um|uh)\W*$", re.I)


def content_words(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _FILLER]


def is_vague(text: str) -> bool:
    """Short, unsure, or empty: "I don't know", "idk, stuff", "maybe school I guess"."""
    n = len(content_words(text))
    return n <= 3 or (bool(_UNSURE.search(text or "")) and n < 9)


def is_rich(text: str) -> bool:
    return not is_vague(text) and len(content_words(text)) >= 9


def emotional(text: str) -> bool:
    return bool(_EMOTIONAL.search(text or "") or tripwire(text or ""))


def mid_story(text: str) -> bool:
    """A long, emotional answer that trails off unfinished ("...and"): let them keep going."""
    return emotional(text) and len(content_words(text)) >= 12 and bool(_TRAILING.search(text or ""))


_SCHOOL_LEAD = re.compile(r"^\W*(?:(?:um|uh|so|oh|yeah|well)\W+)*(?:i go to|i'?m at|i am at|i attend|"
                          r"i'?m a student at|it'?s|i'?m not)?\s*", re.I)


def school_from_text(text: str) -> str | None:
    """'I go to Rice.' -> 'Rice'; None for anything long or unsure."""
    name = _SCHOOL_LEAD.sub("", text or "").strip(" .!?,")
    if not name or _UNSURE.search(name) or len(name.split()) > 6:
        return None
    return name


_DECLINE = re.compile(r"\b(rather not|prefer not|no thanks|no thank you|don'?t want to|skip( it)?|"
                      r"not comfortable|i'?d rather|pass)\b", re.I)


def declines(text: str, bare_no: bool = True) -> bool:
    """"I'd rather not", "no thanks", or (unless after a spell-back, where it means "wrong") a bare "no"."""
    words = _WORD.findall((text or "").lower())
    return bool(_DECLINE.search(text or "")) or (bare_no and words in (["no"], ["nope"], ["nah"]))


# ---- what's done -------------------------------------------------------------

def has_item(state: CallState, item: str) -> bool:
    return all(state.has(f) for f in ITEM_FIELDS.get(item, (item,)))


def answered(item: str | None, learned: set[str]) -> bool:
    """Did the latest package fill `item` (or any of its fields)?"""
    return bool(item) and any(f in learned for f in ITEM_FIELDS.get(item, (item,)))


def done(state: CallState, name: str) -> bool:
    if name == "email":
        return email_done(state)
    return has_item(state, name) or state.tries(name) >= MAX_ATTEMPTS


def email_done(state: CallState) -> bool:
    return state.email_done or state.has("email") or state.tries("email_turns") >= MAX_EMAIL_TURNS


def confident(state: CallState, name: str) -> bool:
    if name == "wants" and state.has("partner_traits"):
        return True     # they named the qualities they want in the other student
    return state.has(name) and state.confidence.get(name, 0.0) >= CONFIDENT


def required_ready(state: CallState) -> bool:
    """Enough to match on: what they're going through (with a theme) and what they want."""
    return confident(state, "situation") and state.has("themes") and confident(state, "wants")


def deepen_moves(state: CallState) -> int:
    return sum(1 for m in state.moves if m != WRAP)


def budget_left(state: CallState) -> int:
    return DEEPEN_MAX_MOVES - deepen_moves(state)


def needs_ai_use(state: CallState) -> bool:
    """AI use is the core of AIEDH: spend one turn on it before REFLECT if it's still empty."""
    return not state.has("ai_use") and not (ONCE_PER_CALL & set(state.moves)) and budget_left(state) > 0


def missing_required(state: CallState) -> list[str]:
    out = []
    if not confident(state, "situation") or not state.has("themes"):
        out.append("situation")
    if not confident(state, "wants"):
        out.append("wants")
    return out


# ---- transitions -------------------------------------------------------------

def advance(state: CallState, now: float | None = None) -> Phase:
    """The phase this turn runs in, given what's known. Never leaves CRISIS."""
    phase = state.phase
    if phase in (Phase.CRISIS, Phase.CLOSING):
        return phase
    if phase is Phase.OPENING:
        phase = Phase.CONTEXT if state.returning else Phase.IDENTITY
    if phase is Phase.IDENTITY and (state.identity_skipped
                                    or all(done(state, f) for f in IDENTITY_ORDER)):
        phase = Phase.CONTEXT
    if phase is Phase.CONTEXT and state.tries("situation") >= 1:
        phase = Phase.DEEPEN
    if phase is Phase.DEEPEN and (budget_left(state) <= 0
                                  or (required_ready(state) and not needs_ai_use(state))):
        phase = Phase.REFLECT
    if phase is Phase.REFLECT and (state.reflect_confirmed or state.tries("reflect") >= MAX_ATTEMPTS):
        phase = Phase.TIMING
    if phase is Phase.TIMING and done(state, "timing"):
        phase = Phase.EMAIL
    if phase is Phase.EMAIL and next_item(state, Phase.EMAIL) == "closing":
        phase = Phase.CLOSING
    return phase


# ---- goals -------------------------------------------------------------------

def next_item(state: CallState, phase: Phase) -> str:
    """What to ask about next in `phase`: a field/item name, "move", "reflect", or "closing"."""
    if phase is Phase.IDENTITY:
        for f in IDENTITY_ORDER:
            if not done(state, f):
                return f
        return "situation"
    if phase is Phase.CONTEXT:
        return "situation"
    if phase is Phase.DEEPEN:
        return "move"
    if phase is Phase.REFLECT:
        return "reflect"
    if phase is Phase.TIMING:
        return "timing"
    if phase is Phase.EMAIL:
        if state.identity_skipped:
            for f in DEFERRED_IDENTITY:
                if not done(state, f):
                    return f
        return "closing" if email_done(state) else "email"
    return "closing"


def item_goal(state: CallState, item: str) -> str:
    if item == "reflect":
        return REFLECT_AGAIN_GOAL if state.tries("reflect") >= 1 else REFLECT_GOAL
    if item == "closing":
        return PRE_CLOSING_GOAL
    if item == "year_major" and state.has("year") != state.has("major"):
        item = "major" if state.has("year") else "year"
    if state.tries(item) < 1 or item not in CONCRETE:
        return ASK[item]
    return CONCRETE[item]


def assume_answered(state: CallState, item: str) -> CallState:
    """A copy of state as if the caller just answered `item` clearly."""
    hyp = dataclasses.replace(state, profile=dict(state.profile), confidence=dict(state.confidence),
                              attempts=dict(state.attempts))
    if item == "reflect":
        hyp.reflect_confirmed = True
    elif item == "situation":
        hyp.attempts["situation"] = max(1, hyp.attempts.get("situation", 0))
    for f in ITEM_FIELDS.get(item, (item,)):
        if f in hyp.profile and not hyp.has(f):
            hyp.profile[f] = "…"
    return hyp


def plan_goal(state: CallState, phase: Phase, asked: str | None) -> tuple[str, str | None, str]:
    """(goal, retry_item, next_item) for this turn, outside CONTEXT/DEEPEN moves.

    `asked` is what Aide's last line asked about, so the caller's latest line is
    presumably the answer. retry_item is set when a vague answer should get a
    concrete follow-up; next_item is what to ask if the answer was clear.
    """
    if asked and asked in ASK and asked != "email" and not done(state, asked):
        hyp = assume_answered(state, asked)
        nxt = next_item(hyp, advance(dataclasses.replace(hyp, phase=phase)))
        if nxt == "move":       # the answer to "what made you call" gets a move, not a goal
            nxt = "situation"
        nxt_goal = item_goal(hyp, nxt)
        retry = CONCRETE.get(asked, ASK[asked])
        partial = ""
        if asked == "year_major" and not state.has("year") and not state.has("major"):
            partial = ' If it gives only one of the two, ask for the other, like "And what are you studying?"'
        goal = (f"Your last question was about {LABEL[asked]}. If their latest line answers it, don't "
                f"repeat their answer back, just go on: {nxt_goal}{partial} If it was vague: {retry}")
        return goal, asked, nxt
    nxt = next_item(state, phase)
    if asked in ASK and asked != "email" and state.tries(asked) >= MAX_ATTEMPTS:
        # Out of retries, but the caller is still answering it; make sure it gets recorded.
        return (f"Their latest line answers your last question, about {LABEL[asked]}. "
                f"Respond briefly, then: {item_goal(state, nxt)}"), None, nxt
    return item_goal(state, nxt), None, nxt


# ---- moves -------------------------------------------------------------------

def unprobed_theme(state: CallState) -> str | None:
    for theme in state.profile.get("themes") or []:
        if theme in THEME_PROBES and theme not in state.probed:
            return theme
    return None


def choose_move(state: CallState, text: str) -> str:
    """The move for Aide's reply to `text` in CONTEXT/DEEPEN (WRAP = go to REFLECT)."""
    moves = state.moves
    last = moves[-1] if moves else None
    left = budget_left(state)
    if left <= 0:
        return WRAP
    if required_ready(state):
        return ASK_AI_USE if needs_ai_use(state) else WRAP

    def ok(move: str) -> bool:
        if move == last and move != FOLLOW_THREAD:
            return False
        return not (move in ONCE_PER_CALL and ONCE_PER_CALL & set(moves))   # one chatbot question per call

    wants_open = not confident(state, "wants")
    vague = is_vague(text)
    if vague:
        if last == OFFER_CHOICES:
            # Never follow up a vague answer twice in a row: take what we have and move on.
            for move in (ASK_WANTS if wants_open else None, THEME_PROBE if unprobed_theme(state) else None):
                if move and ok(move):
                    return move
            return WRAP if state.has("situation") or left <= 1 else FOLLOW_THREAD
        if not state.has("situation") and ok(CHATBOT_DOORWAY):
            return CHATBOT_DOORWAY
        if ok(OFFER_CHOICES):
            return OFFER_CHOICES
    if mid_story(text) and ok(LET_THEM_TALK) and left >= 3:
        return LET_THEM_TALK
    # Leave room for wants: by the last two questions, ask it if it's still open.
    if wants_open and left <= 2 and state.has("situation") and ok(ASK_WANTS):
        return ASK_WANTS
    if is_rich(text):
        return FOLLOW_THREAD
    if wants_open and state.has("situation") and ok(ASK_WANTS):
        return ASK_WANTS
    if unprobed_theme(state) and ok(THEME_PROBE):
        return THEME_PROBE
    return FOLLOW_THREAD


def _missing_hint(state: CallState) -> str:
    missing = missing_required(state)
    if "situation" in missing:
        return "what's really going on for them"
    if "wants" in missing:
        return "what kind of student would help them"
    if needs_ai_use(state):
        return "how they use AI chatbots"
    return "what would help them most"


def _wrap_clause(state: CallState, move: str) -> str:
    """The early exit a move carries, since the record lags the latest line by a turn."""
    if offered_alternative(state, move) is None:
        return ""
    if needs_ai_use(state) and budget_left(state) > 1:
        return (" If their latest line already makes clear what they're going through and what "
                "they'd want from another student, but you still don't know how they use AI chatbots, "
                f"instead ask what they usually end up talking to the chatbot about (move {ASK_AI_USE}).")
    return (" If their latest line already makes clear what they're going through and what they'd "
            f"want from another student, instead do this (move {WRAP}): {REFLECT_GOAL}")


def offered_alternative(state: CallState, move: str) -> str | None:
    """The move the goal lets the model switch to (see _wrap_clause)."""
    if move in (WRAP, ASK_AI_USE, CHATBOT_DOORWAY, OFFER_CHOICES):
        return None     # nothing to switch to; the doorway and choices follow a vague answer
    return ASK_AI_USE if needs_ai_use(state) and budget_left(state) > 1 else WRAP


def move_goal(state: CallState, move: str) -> str:
    if move == WRAP:
        return item_goal(state, "reflect")
    hint = _missing_hint(state)
    if move == FOLLOW_THREAD:
        goal = ("Pick up the most specific or emotional thing they just said, using their own words, "
                f"and ask about it in a way that also helps you learn {hint}, like \"That extra semester "
                f"is a lot. What part of it weighs on you most?\"")
    elif move == OFFER_CHOICES:
        goal = ("Their answer was short or unsure. Don't ask the same thing again. Offer two or three "
                f"concrete options, tailored to what you know so far, about {hint}, in one either-or question, "
                'like "Is it more school stuff, people stuff, or something back home?"')
    elif move == CHATBOT_DOORWAY:
        goal = ('Ask, in your own natural words: "What was the last thing you talked to a chatbot about?"')
    elif move == THEME_PROBE:
        theme = unprobed_theme(state)
        question, reveals = THEME_PROBES[theme][0]
        goal = (f'Ask this follow-up about {theme.replace("_", " ")}, adapted to their own words: '
                f'"{question}" (Their answer shows {reveals}; fill those fields from it.)')
    elif move == ASK_WANTS:
        goal = ("Ask what would make another student the right person for them, framed naturally, like: "
                '"If we found you someone tonight, what would make them the right person?"')
    elif move == LET_THEM_TALK:
        goal = ("They're in the middle of something big. In a few words, invite them to keep going, "
                'like "That\'s a lot. What else has it been like?" No new topic.')
    elif move == ASK_AI_USE:
        goal = 'Ask, naturally: "What do you usually end up talking to the chatbot about?"'
    else:
        raise ValueError(move)
    return goal + _wrap_clause(state, move)


_LATER_RE = re.compile(r"\b(later|tomorrow|text( me)?|another (day|time)|not (right )?now)\b", re.I)
_TIMING_WORDS = [
    ("now", re.compile(r"\b(right now|now|tonight|asap|as soon as)\b", re.I)),
    ("later", re.compile(r"\b(later|tomorrow|another (day|time)|this week|next week|weekend)\b", re.I)),
    ("either", re.compile(r"\b(either|whenever|doesn'?t matter|any ?time|both|don'?t mind|no preference)\b", re.I)),
]


def timing_from_text(text: str) -> str | None:
    """Read of the TIMING answer, so the next line needs no model call. The question is
    yes/no ("Do you want to speak to someone right now?"): yes is now, no is later.
    "Later" or "text" wins over a yes ("sure, text me later"), and a no wins over "now"
    ("no, not right now")."""
    if _LATER_RE.search(text or "") or is_no(text):
        return "later"
    if is_yes(text):
        return "now"
    hits = [value for value, rx in _TIMING_WORDS if rx.search(text or "")]
    if "either" in hits or len(hits) > 1:
        return "either"
    return hits[0] if hits else None
