"""Fast, no-AI checks on caller text: the crisis tripwire and yes/no readers.

The tripwire never triggers the crisis script by itself. It only makes the next
turn's goal a direct, calm safety question.
"""

from __future__ import annotations

import re

# Phrases that warrant asking directly whether the caller is safe. Kept specific
# so everyday hyperbole ("this exam is killing me") doesn't fire.
_TRIPWIRE = [
    r"\bkill(?:ing)? my ?self\b",
    r"\bsuicid\w*",
    r"\bend (?:it all|my life|things)\b",
    r"\b(?:want|wanna|wanted|going) to die\b",
    r"\bwish i (?:was|were) dead\b",
    r"\bbetter off (?:dead|without me)\b",
    r"\b(?:hurt|harm|cut|cutting|hurting|harming) my ?self\b",
    r"\bself[- ]?harm\w*",
    r"\boverdos\w*",
    r"\bno (?:reason|point) (?:to|in) (?:live|living|being here|going on)\b",
    r"\b(?:don'?t|do not) want to (?:be here|be alive|live|wake up)(?: anymore)?\b",
    r"\bnot (?:want|wanting) to (?:be here|be alive|live) anymore\b",
    r"\bcan'?t (?:go on|do this anymore|keep going)\b",
    r"\b(?:take|taking) my (?:own )?life\b",
]
_TRIPWIRE_RE = re.compile("|".join(_TRIPWIRE), re.IGNORECASE)


def tripwire(text: str) -> str | None:
    """The matched phrase, or None. Negated phrases ("I don't want to die") fire
    too: a false positive costs one gentle question, a miss costs far more."""
    m = _TRIPWIRE_RE.search(text or "")
    return m.group(0) if m else None


_WORD = re.compile(r"[a-z']+")
_YES = {"yes", "yeah", "yep", "yup", "ya", "yea", "correct", "right", "exactly", "sure",
        "mhm", "mm-hmm", "uh-huh", "affirmative", "true", "definitely", "absolutely", "totally"}
_YES_PHRASES = ("that's right", "thats right", "that is right", "you got it", "sounds right",
                "that's it", "pretty much", "spot on", "that's correct", "it is")
_NO = {"no", "nope", "nah", "not", "wrong", "incorrect", "never"}
_CORRECTION = {"but", "actually", "except", "although", "though", "wait"}
# Answers to "are you thinking about hurting yourself?" that must be treated as yes.
_SAFETY_PARTIAL = ("kind of", "kinda", "sort of", "sometimes", "maybe", "i guess", "a little",
                   "i have been", "i've been", "i think so", "a bit", "lately",
                   "don't know", "not sure", "idk")
_SAFE = ("i'm safe", "i am safe", "i'm okay", "i am okay", "i'm ok", "i'm fine", "i am fine",
         "i'm good", "i'm alright", "i'm all right")


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("mm-hmm", "mhm").replace("uh-huh", "mhm"))


def is_yes(text: str) -> bool:
    """A clean confirmation: affirmative, with no "no" and no correction."""
    lowered = (text or "").lower()
    words = _words(lowered)
    if not words or any(w in _NO for w in words) or any(w in _CORRECTION for w in words):
        return False
    return words[0] in _YES or any(p in lowered for p in _YES_PHRASES) \
        or (len(words) <= 3 and any(w in _YES for w in words))


def is_no(text: str) -> bool:
    words = _words(text)
    return bool(words) and (words[0] in _NO or "not really" in " ".join(words[:3]))


def safety_yes(text: str) -> bool:
    """Reply to the safety question. Anything short of a clear no counts as yes."""
    lowered = (text or "").lower()
    if tripwire(lowered):
        return True
    words = _words(lowered)
    if not words:
        return False
    if words[0] in _NO and not any(p in lowered for p in _SAFETY_PARTIAL):
        return False
    if any(p in lowered for p in _SAFE) and words[0] not in _YES:
        return False
    return words[0] in _YES or any(p in lowered for p in _SAFETY_PARTIAL)
