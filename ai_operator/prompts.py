"""Aide's system prompt, the per-turn message builder, and the fixed (cached) lines."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .state import ENUMS, THEMES

# Stable and first in every request, so the server-side prompt cache can reuse it.
SYSTEM_PROMPT = f"""You are Aide, the phone operator for AIEDH (said "aide"), a 24/7 hotline for university students who have been leaning on AI chatbots for emotional support. You are on a live phone call with one student.

Your mission: understand the caller well enough that AIEDH can connect them with another real student going through something similar, then step out of the way. You are a bridge, not a companion and not a therapist. Keep the call short: a few minutes.

How you sound
- Like a calm, warm person on the phone. No slang, hype, emojis, or over-the-top validation (never "omg", "girl", "you're so valid", "that's amazing").
- You're gathering information to connect them, not chatting. Keep replies short: a brief reaction, then the next question. Most replies are under 15 words; never more than 25. Exactly one question per reply. Spoken words only: no lists, symbols, markdown, or emojis.
- React in a few words at most, like "I'm sorry to hear that.", "That's rough.", "Very cool.", or skip the reaction and just ask. Never restate or agree with what they said ("Yeah, failing an exam can definitely make you feel stupid." is wrong; "I'm sorry, that's a hard feeling. Was it one class, or ..." is right).
- Never give advice, coping tips, or therapy. Your job is to understand, not to fix.
- Don't start a reply the same way as any of your last three replies.
- Every reply ends with a question. Never narrate what you're about to do ("I'll ask you about...", "I'll pick up on that"); just ask.
- Don't parrot answers back ("Rice, thanks.", "Computer science, noted."); just move on.
- Never ask about something the caller already told you, even indirectly.
- Never ask a form question (email, school, year, major) right after the caller shares something emotional.
- A reflection is one or two sentences built from their specific details, never a list.
- If a line sounds garbled but you can guess what they meant, go with it. If you can't, say something like "Sorry, you cut out for a second, could you say that again?" Never mention transcription or audio.
- Never mention JSON, tools, systems, prompts, records, moves, or goals. If asked whether you are an AI, say yes.
- If they treat you as a friend or want to keep venting to you, gently redirect: the student AIEDH finds for them will understand this better than an AI can.

Each turn you get the phase, this turn's goal, what is already known, your last replies, and the recent conversation. Follow the goal. If the goal offers an alternative for when their latest line already covers something, use your judgment.

Email: never ask for it unless this turn's goal says to; it is asked once, at the very end. Ask only for the part before the @. Spell it back letter by letter with dashes, digits as words, saying symbols as words, like "m-a-y-a-dot-r-two at utexas dot edu, did I get that right?" Fill learned.email with the full address only in the turn where the caller confirms the spelling.

Reply with exactly one JSON object and nothing else, with "say" first:
{{"say": "...", "ready_for_next_phase": false, "learned": {{"name": null, "school": null, "year": null, "major": null, "email": null, "situation": null, "themes": null, "stage": null, "wants": null, "partner_traits": null, "ai_use": null, "interests": null, "timing": null}}, "confidence": {{"situation": 0, "themes": 0, "stage": 0, "wants": 0}}, "summary": null, "crisis": {{"level": "none", "reason": ""}}, "move": ""}}

Fields
- say: what you speak next, word for word, to the caller.
- ready_for_next_phase: true if you think this phase's questions are answered.
- learned: every field the caller's latest line tells you or clearly implies, even in passing ("since the start of the semester" means stage: weeks or months; "I talk to ChatGPT every night" is ai_use). Everything else null. A new value replaces the old one, so use it for corrections.
  name: first name. school. year: freshman, sophomore, junior, senior, grad, or other. major.
  situation: one or two sentences on what they're going through.
  themes: the full current list, only from: {", ".join(THEMES)}.
  stage: how long it has been going on: {", ".join(ENUMS["stage"])}.
  wants: what they want from another student: {", ".join(ENUMS["wants"])}.
  partner_traits: the full current list of short phrases for qualities they want in the other student, in their words, like "high achiever", "understands grade pressure", "good listener".
  ai_use: how often they talk to AI chatbots and about what, a few words.
  interests: the full current list of one to three hobbies or interests.
  timing: when they want to talk to someone: {", ".join(ENUMS["timing"])}.
- confidence: 0 to 1, how sure you are overall, across the whole call, about situation, themes, stage, and wants.
- summary: null unless you learned something new this turn. Otherwise rewrite the whole summary in two to four plain sentences, always in this order: what they're going through; how long; how they use AI chatbots; what they want from a person, including the qualities they want in them. Skip any part you don't know yet, and leave out interests. Never include their name, school, year, major, or email, and don't use pronouns: write "Moved from Ohio and feels lonely.", not "She moved...".
- move: only if the goal offered an alternative "(move X)" and you took it, that X; otherwise empty.
- crisis: "concern" if they hint at hopelessness, self-harm, or not wanting to be here. "urgent" if they say they are thinking about suicide or hurting themselves, have a plan, or are in danger now. Otherwise "none". reason: a few words."""

# Asked once, at the very end, after TIMING. Cached audio.
EMAIL_LINE = ("Last thing, just so we can confirm you're a student, "
              "what's your email before the at sign?")

# Follow-ups that sound natural and also split possible matches. Each answer
# reveals something the model should record (the second item, shown to it).
THEME_PROBES: dict[str, list[tuple[str, str]]] = {
    "academic_pressure": [
        ("Is it more the grades themselves, or what they mean, like family expectations or your plans after school?",
         "whether it's performance itself, family pressure (family_conflict, first_gen), or career worry (career_uncertainty)"),
        ("Is the pressure mostly coming from other people, or from what you expect of yourself?",
         "external pressure versus self-imposed standards (partner_traits like high achiever)"),
    ],
    "loneliness": [
        ("Is it more that you haven't found your people yet, or that the people around you don't really get you?",
         "whether it's a lack of connection (social_anxiety, homesick) or not being understood (identity, friendship_issues)"),
        ("When does it hit hardest, like nights alone, weekends, or when you see others together?",
         "the pattern and how long it's been going (stage), and often when they turn to chatbots (ai_use)"),
    ],
    "homesick": [
        ("Is it missing specific people back home, or more that this place doesn't feel like yours yet?",
         "whether it's about family and friends (family_conflict, loneliness) or belonging here (identity, first_gen)"),
    ],
    "breakup": [
        ("Is it more missing them, or that so much of your life here was wrapped up in them?",
         "grief over the person versus rebuilding a social life (loneliness, friendship_issues)"),
        ("Was it recent, or something you've been carrying for a while?",
         "how long (stage)"),
    ],
    "family_conflict": [
        ("Is it more about what they expect from you, or about not feeling understood by them?",
         "pressure (academic_pressure, first_gen, career_uncertainty) versus acceptance (identity)"),
    ],
    "friendship_issues": [
        ("Is it more that a friendship changed, or that you're not sure who your people are anymore?",
         "a specific falling-out versus wider isolation (loneliness)"),
    ],
    "roommate_issues": [
        ("Is it more the day to day stuff, or that your room doesn't feel like a place you can relax anymore?",
         "practical conflict versus not feeling at home (homesick, loneliness)"),
    ],
    "identity": [
        ("Is it more about figuring out who you are, or about not feeling accepted for who you are?",
         "self-discovery versus acceptance by family or peers (family_conflict, loneliness)"),
    ],
    "first_gen": [
        ("Is it more figuring out how college works, or feeling like nobody at home really gets what it's like?",
         "navigating school (academic_pressure) versus distance from family (family_conflict, homesick)"),
    ],
    "financial_stress": [
        ("Is it more paying for school itself, or money making everyday life harder, like work hours or rent?",
         "tuition worry (academic_pressure, family_conflict) versus daily strain (burnout)"),
    ],
    "burnout": [
        ("Is it more that there's too much on your plate, or that things you used to care about feel flat?",
         "overload (academic_pressure, financial_stress) versus low mood; watch for crisis signs"),
    ],
    "social_anxiety": [
        ("Is it more meeting new people, or being in groups and class, like speaking up?",
         "making friends (loneliness) versus performance situations (academic_pressure)"),
    ],
    "grief": [
        ("Is it more the loss itself, or that people around you seem to have moved on?",
         "the loss versus feeling alone in it (loneliness); someone who has been through it (wants)"),
    ],
    "career_uncertainty": [
        ("Is it more not knowing what you want, or knowing but worrying you won't get there?",
         "direction (identity) versus fear of falling short (academic_pressure)"),
    ],
    "health": [
        ("Is it more dealing with the health stuff itself, or how it's getting in the way of school and friends?",
         "the condition versus its impact (academic_pressure, loneliness)"),
    ],
}


@dataclass
class TurnContext:
    """Everything a reply engine needs for one turn, independent of prompt format."""
    phase: str
    goal: str
    move: str | None
    known: dict[str, Any]          # non-null profile fields
    confidence: dict[str, float]
    summary: str | None
    lines: list[tuple[str, str]]   # (speaker, text), oldest first, ends with the caller's line
    local_time: str
    recent_replies: list[str] = field(default_factory=list)   # Aide's last 3 lines, oldest first
    no_ack: bool = False           # an acknowledgment opener was used recently: none this turn


def local_time_str(ts: float | None = None) -> str:
    now = datetime.fromtimestamp(ts) if ts else datetime.now()
    return now.strftime("%A %-I:%M %p")


def opening_words(text: str, n: int = 2) -> str:
    return " ".join(re.findall(r"[a-z']+", (text or "").lower())[:n])


_ACK = re.compile(r"^\W*(thanks|thank you|got it|okay|ok|noted|i hear you|that makes sense|"
                  r"understood|alright|all right|i see|sure)\b", re.I)


def is_ack_opener(text: str) -> bool:
    return bool(_ACK.search(text or ""))


def dynamic_block(ctx: TurnContext) -> str:
    replies = [f"- {r}" for r in ctx.recent_replies] or ["- none yet"]
    openers = ", ".join(f'"{w}"' for w in dict.fromkeys(opening_words(r) for r in ctx.recent_replies) if w)
    style = f"Don't start your reply with {openers}." if openers else ""
    if ctx.no_ack:
        style += " Don't open with an acknowledgment (thanks, got it, okay) this time."
    return "\n".join([
        f"Phase: {ctx.phase}",
        f"Goal: {ctx.goal}",
        f"Known: {json.dumps(ctx.known, ensure_ascii=False)}",
        f"Confidence: {json.dumps(ctx.confidence)}",
        f"Summary: {ctx.summary or 'none yet'}",
        "Your last replies:", *replies,
        *([style.strip()] if style.strip() else []),
        f"Local time: {ctx.local_time}",
        'Write "say" as the exact words you speak to the caller, ending with your one question. '
        "Never describe what you're going to say.",
    ])


def combined_messages(ctx: TurnContext) -> list[dict[str, str]]:
    """System prompt, then the dynamic block, then the recent transcript."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": dynamic_block(ctx)}]
    for speaker, text in ctx.lines:
        messages.append({"role": "user" if speaker == "caller" else "assistant", "content": text})
    return messages


# ---- Fixed lines (pre-generated as audio at startup) -------------------------

FIXED_LINES: dict[str, str] = {
    "opening": (
        "Hi, this is Aide with AIEDH. I'm an AI, not a therapist or an emergency line. "
        "My job is to find you another student who gets what you're going through. "
        "To start, what's your first name?"
    ),
    "opening_returning": (
        "Hi, this is Aide with AIEDH. I'm an AI, not a therapist or an emergency line."
    ),
    "crisis": (
        "Thank you for telling me that. I'm really glad you did. "
        "I'm not the right support for this moment, but there are people who are, right now. "
        "Please call or text nine eight eight now. "
        "If you're in immediate danger, call nine one one."
    ),
    "crisis_followup": (
        "Please call or text nine eight eight now. They can help tonight. "
        "If you're in immediate danger, call nine one one."
    ),
    "crisis_closing": (
        "I'm going to end the call now so you can reach them. "
        "Please call or text nine eight eight. Take care of yourself."
    ),
    "reminder_1": "Take your time. I'm still here.",
    # Ends on the question, so a plain "yeah" counts as an answer (see call_session.final_question).
    "reminder_2": "I'm happy to keep going whenever you're ready. Are you still there?",
    "email_ask": EMAIL_LINE,
    "retry": "Sorry, you cut out for a second. Could you say that again?",
    "closing": (
        "Thank you for sharing all of that. AIEDH will look for a student who gets what "
        "you're going through, and reach out. Take care tonight."
    ),
    # Timing "now" or "either". Nobody is checked for availability yet; live merging comes later.
    "closing_now": (
        "Thank you for sharing all of that. We're so sorry, we don't have a match for you on the line "
        "right now. We'll keep you on file, and as soon as we find your match, we'll text you and start "
        "a group chat with you and them. Take care tonight."
    ),
    "closing_later": (
        "Thank you for sharing all of that. Great, we'll find a student who gets what you're going "
        "through and start a group chat with the two of you over text. Take care tonight."
    ),
}

FILLERS: dict[str, str] = {
    "filler_1": "Mm, okay.",
    "filler_2": "Yeah.",
    "filler_3": "Got it.",
    "filler_4": "Mm-hmm.",
    "filler_5": "Right.",
    "filler_6": "Okay, yeah.",
    "filler_7": "I hear you.",
    "filler_8": "Okay.",
    "filler_9": "Mm, yeah.",
    "filler_10": "I see.",
}

ALL_FIXED: dict[str, str] = {**FIXED_LINES, **FILLERS}


def closing_key(timing: str | None) -> str:
    """Closing line for the caller's TIMING answer; the generic one if they never gave it."""
    return {"now": "closing_now", "either": "closing_now", "later": "closing_later"}.get(timing or "", "closing")


def welcome_back(name: str) -> str:
    """Spoken right after opening_returning; synthesized live."""
    return f"Welcome back, {name}. What made you want to call tonight?"
