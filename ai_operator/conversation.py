"""The per-call conversation policy, shared by phone calls and chat_test.py.

    plan = conv.plan(caller_text)      # pure: no state changes
    conv.commit(plan)                  # the turn is really happening
    if plan.fixed:   play FIXED_LINES[plan.fixed]           (no model call)
    else:            reply = conv.start_reply(plan); stream reply.chunks()
                     conv.apply_package(await reply.package, plan)
    conv.operator_said(text_that_actually_played)

plan() being pure is what makes the early start work: plan on the partial text
at speech end, start the reply, then plan again on the final text and keep the
reply only if same_turn() says nothing meaningful changed.
"""

from __future__ import annotations

import dataclasses
import difflib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import planner
from .brain import Package, Reply, ReplyEngine
from .planner import ASK, PRE_CLOSING_GOAL, REINTRO_GOAL, SAFETY_GOAL, WRAP
from .prompts import TurnContext, closing_key, is_ack_opener, local_time_str, welcome_back
from .safety import is_yes, safety_yes, tripwire
from .state import CONF_FIELDS, IDENTITY_FIELDS, CallState, Phase

log = logging.getLogger("aide")

HISTORY_LINES = 12
# Seconds of silence before reminder 1, then reminder 2, then the closing.
SILENCE_STEPS = (12.0, 12.0, 15.0)
CRISIS_SILENCE_SECONDS = 30.0


@dataclass
class TurnPlan:
    caller_text: str
    phase: Phase
    goal: str | None = None
    fixed: str | None = None            # FIXED_LINES key: play it, no model call
    end_call: bool = False              # hang up after this line has played
    safety: bool = False                # this turn asks the safety question
    asked: str | None = None            # what the caller's line is answering
    move: str | None = None             # CONTEXT/DEEPEN: the move this reply makes (planner.MOVES)
    alternative: str | None = None      # the move the goal lets the model switch to instead
    branch: tuple[str | None, str | None] = (None, None)
    profile: dict[str, Any] = field(default_factory=dict)   # code-side profile updates
    attempts: list[str] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)     # CallState attributes to set
    crisis: tuple[str, str] | None = None
    email_confirmed: str = ""           # spelled letters the caller just confirmed
    notes: list[str] = field(default_factory=list)
    committed: bool = False
    turn: int = 0                       # state.turn_count once committed (orders late packages)

    @property
    def needs_model(self) -> bool:
        return self.fixed is None


@dataclass
class PackageResult:
    changed: bool = False
    crisis_now: bool = False     # switch to CRISIS: stop Aide's line, play the crisis line
    closing_now: bool = False    # TIMING answered: play the closing after this line
    move: str | None = None      # the move the model actually made, if it differs from the plan
    notes: list[str] = field(default_factory=list)


def _norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


_FILLER_WORDS = {"um", "uh", "like", "so", "and", "yeah", "mm", "hmm", "oh", "okay", "ok"}


def texts_match(a: str, b: str) -> bool:
    """Would a reply to `a` also fit `b`? (For the early start on speech end.)"""
    wa, wb = _norm_words(a), _norm_words(b)
    if wa == wb:
        return True
    core_a = [w for w in wa if w not in _FILLER_WORDS]
    core_b = [w for w in wb if w not in _FILLER_WORDS]
    if core_a == core_b:
        return True
    ratio = difflib.SequenceMatcher(None, core_a, core_b).ratio()
    return ratio >= 0.9 and abs(len(core_a) - len(core_b)) <= 1


class Conversation:
    def __init__(self, state: CallState, engine: ReplyEngine,
                 on_change: Callable[[CallState], None] | None = None) -> None:
        self.state = state
        self.engine = engine
        self._on_change = on_change

    @property
    def silence_timeout(self) -> float:
        if self.state.phase is Phase.CRISIS:
            return CRISIS_SILENCE_SECONDS
        return SILENCE_STEPS[min(self.state.silence_count, len(SILENCE_STEPS) - 1)]

    def _changed(self) -> None:
        if self._on_change:
            self._on_change(self.state)

    # ---- opening -----------------------------------------------------------

    def opening(self) -> tuple[str, str | None]:
        """(fixed line key, extra text to synthesize live or None)."""
        s = self.state
        name = s.profile.get("name")
        if s.returning and name:
            s.set_phase(Phase.CONTEXT)
            s.branch = (None, "situation")
            self._changed()
            return "opening_returning", welcome_back(name)
        s.set_phase(Phase.IDENTITY)
        s.branch = (None, "name")
        self._changed()
        return "opening", None

    # ---- planning ----------------------------------------------------------

    def resolve_asked(self) -> str | None:
        """What Aide's last line asked about, judging by what the model learned."""
        s = self.state
        retry, nxt = s.branch
        if retry and retry != "email" and not planner.done(s, retry) and \
                not (planner.answered(retry, s.last_learned) and planner.has_item(s, retry)):
            return retry     # vague answer: the model asked a concrete follow-up
        if nxt in ASK and nxt != "email" and planner.done(s, nxt):
            # The caller already covered it, so the model moved on to the next open item.
            phase = planner.advance(s)
            return planner.next_item(dataclasses.replace(s, phase=phase), phase)
        return nxt

    def same_turn(self, a: TurnPlan, b: TurnPlan) -> bool:
        return (a.fixed == b.fixed and a.goal == b.goal and a.phase == b.phase
                and texts_match(a.caller_text, b.caller_text))

    def plan(self, text: str, now: float | None = None) -> TurnPlan:
        s = self.state
        text = " ".join((text or "").split())
        p = TurnPlan(caller_text=text, phase=s.phase)

        if s.phase is Phase.CRISIS:
            p.fixed = "crisis_followup"
            p.notes.append("CRISIS: fixed follow-up line, no model call")
            return p
        if s.phase is Phase.CLOSING:
            p.fixed = closing_key(s.profile.get("timing"))
            p.end_call = True
            return p

        if s.safety_pending:
            p.flags["safety_pending"] = False
            if safety_yes(text):
                p.phase = Phase.CRISIS
                p.fixed = "crisis"
                p.crisis = ("urgent", "said yes to the safety question")
                p.notes.append("safety question answered yes -> CRISIS")
                return p
            p.notes.append("safety question answered no")

        trip = tripwire(text)
        if trip:
            p.crisis = ("concern", f"tripwire: {trip}")
            p.notes.append(f"tripwire fired on {trip!r}")

        heard = self._question_heard()
        direct_goal = None     # a goal decided here, not by planner.plan_goal
        if s.email_spelled:
            # Aide spelled the email back; this line confirms or corrects it.
            p.asked = "email"
            p.attempts.append("email_turns")
            p.flags.update(email_spelled="", email_spelled_domain="")
            if is_yes(text):
                # Committed here, not by the model, so a confirmed email is never asked again.
                p.email_confirmed = s.email_spelled
                domain = s.email_spelled_domain or planner.email_domain(s.profile.get("school"))
                p.profile["email"] = f"{s.email_spelled}@{domain}" if domain else s.email_spelled
                p.flags["email_done"] = True
                p.notes.append(f"email confirmed: {p.profile['email']}")
            elif planner.declines(text, bare_no=False):
                p.flags["email_done"] = True
                p.notes.append("caller declined to give an email")
            else:
                direct_goal = ("They corrected the email spelling. Spell the corrected version back "
                               "with dashes and ask if it's right now.")
                p.branch = (None, "email")
        elif s.branch[1] == "email" and self.resolve_asked() == "email" and not planner.email_done(s):
            p.asked = "email"
            if heard:
                p.attempts.append("email_turns")
            if planner.declines(text):
                p.flags["email_done"] = True
                p.notes.append("caller declined to give an email")
            else:
                direct_goal = ("Their latest line should be the part of their email before the @."
                               f"{self._email_hint()} If they'd rather not share it, just thank them.")
                p.branch = (None, "email")
        else:
            asked = self.resolve_asked()
            p.asked = asked
            # A question only counts as asked once the caller heard it: an opening cut off
            # by noise, or two caller lines in a row, mustn't use up its tries.
            if (asked in ASK or asked == "reflect") and \
                    (heard or (asked == "situation" and not planner.is_vague(text))):
                p.attempts.append(asked)
            if asked == "reflect" and is_yes(text):
                p.flags["reflect_confirmed"] = True
                p.notes.append("summary confirmed")
            if asked == "timing" or s.phase is Phase.TIMING:
                timing = planner.timing_from_text(text)
                if timing:
                    p.profile["timing"] = timing
                    p.notes.append(f"timing={timing} (keyword)")
            if s.phase is Phase.IDENTITY and asked in planner.IDENTITY_ITEMS and planner.emotional(text):
                # Never follow an emotional share with a form question: the rest of
                # IDENTITY moves to the end of the call, and this line is the context answer.
                p.flags["identity_skipped"] = True
                if "situation" not in p.attempts:
                    p.attempts.append("situation")
                p.notes.append("caller opened up during IDENTITY: skipping to the conversation")

        draft = self._draft(p)
        draft.turn_count += 1
        phase = planner.advance(draft, now)
        draft.phase = phase
        p.phase = phase

        if trip or s.safety_needed:
            # Safety comes before anything else, including the closing.
            if phase is Phase.CLOSING:
                phase = p.phase = s.phase
            p.safety = True
            p.goal = SAFETY_GOAL
            p.flags["safety_needed"] = ""
            p.branch = (None, planner.next_item(draft, phase))
            return p

        if phase is Phase.CLOSING:
            p.fixed = closing_key(draft.profile.get("timing"))
            p.end_call = True
            return p

        if direct_goal:
            p.goal = direct_goal
            return p

        if phase is Phase.EMAIL and s.phase is Phase.TIMING and p.profile.get("timing") \
                and planner.next_item(draft, phase) == "email":
            # Timing read by keyword: the cached email question, no model call.
            p.fixed = "email_ask"
            p.branch = (None, "email")
            return p

        if phase is Phase.DEEPEN:
            move = planner.choose_move(draft, text)
            p.move = move
            if move == WRAP:
                p.phase = Phase.REFLECT
                draft.phase = Phase.REFLECT
            elif move == planner.THEME_PROBE:
                p.flags["probed"] = s.probed + [planner.unprobed_theme(draft)]
            p.goal = planner.move_goal(draft, move)
            p.alternative = planner.offered_alternative(draft, move)
            p.branch = (None, "reflect" if move == WRAP else "move")
            p.notes.append(f"move {move}")
            return p
        if phase is Phase.REFLECT and s.phase in (Phase.CONTEXT, Phase.DEEPEN):
            p.move = WRAP
            p.notes.append(f"move {WRAP}")

        goal, retry, nxt = planner.plan_goal(draft, phase, p.asked)
        if p.asked == "name" and not heard and not self._asked_anything():
            # The opening was cut off before its question.
            goal = (f"{REINTRO_GOAL} If their latest line already gives their first name, skip the "
                    f"introduction: respond briefly, then: {ASK['school']}")
            retry, nxt = "name", "school"
        p.goal = goal
        p.branch = (retry, nxt)
        return p

    def _question_heard(self) -> bool:
        """Did Aide ask something since the caller's last line (and was it heard, not cut off)?"""
        for line in reversed(self.state.transcript):
            return line.speaker == "operator" and "?" in line.text
        return False

    def _asked_anything(self) -> bool:
        return any(line.speaker == "operator" and "?" in line.text for line in self.state.transcript)

    def _email_hint(self) -> str:
        domain = planner.email_domain(self.state.profile.get("school"))
        where = f" Their address ends in @{domain}." if domain else ""
        return f"{where} Spell it back with dashes and ask if it's right."

    def _draft(self, p: TurnPlan) -> CallState:
        s = self.state
        d = dataclasses.replace(s, profile={**s.profile, **p.profile},
                                confidence=dict(s.confidence), attempts=dict(s.attempts))
        for name in p.attempts:
            d.attempts[name] = d.attempts.get(name, 0) + 1
        for key, value in p.flags.items():
            setattr(d, key, value)
        return d

    # ---- commit ------------------------------------------------------------

    def commit(self, plan: TurnPlan, ts: float | None = None) -> None:
        s = self.state
        if plan.caller_text:
            s.add_line("caller", plan.caller_text, ts)
        s.turn_count += 1
        plan.turn = s.turn_count
        s.silence_count = 0
        for name in plan.attempts:
            s.attempts[name] = s.attempts.get(name, 0) + 1
        s.profile.update(plan.profile)
        for name in plan.profile:
            s.field_turns[name] = plan.turn
        for key, value in plan.flags.items():
            setattr(s, key, value)
        if plan.crisis:
            s.raise_crisis(*plan.crisis)
        s.set_phase(plan.phase)
        s.branch = plan.branch
        s.last_learned = set(plan.profile)
        if plan.move:
            s.moves.append(plan.move)
        if plan.safety:
            s.safety_pending = True
        plan.committed = True
        for note in plan.notes:
            log.info("[%s] %s", s.call_id[-6:], note)
        self._changed()

    # ---- model reply -------------------------------------------------------

    def context(self, plan: TurnPlan) -> TurnContext:
        s = self.state
        known = {k: v for k, v in {**s.profile, **plan.profile}.items()
                 if v is not None and v != [] and v != ""}
        history = [(line.speaker, line.text) for line in s.transcript]
        if not plan.committed:
            history.append(("caller", plan.caller_text))
        replies = [line.text for line in s.transcript if line.speaker == "operator"][-3:]
        return TurnContext(
            phase=plan.phase.value,
            goal=plan.goal or PRE_CLOSING_GOAL,
            move=plan.move,
            recent_replies=replies,
            # At most one reply in three opens with "thanks" / "got it".
            no_ack=any(is_ack_opener(r) for r in replies[-2:]),
            known=known,
            confidence={k: round(s.confidence.get(k, 0.0), 2) for k in CONF_FIELDS},
            summary=s.summary,
            lines=history[-HISTORY_LINES:],
            local_time=local_time_str(),
        )

    def start_reply(self, plan: TurnPlan) -> Reply:
        assert plan.needs_model, "fixed lines never call the model"
        return self.engine.start(self.context(plan))

    def apply_package(self, pkg: Package | None, plan: TurnPlan) -> PackageResult:
        """Merge a turn's analysis. null means skip; a value replaces the old one.

        Packages merge in turn order: one that arrives late (its reply was
        interrupted) still fills fields, but never overwrites a value a newer
        turn's package already set. Crisis levels only go up, so those always apply.
        """
        s = self.state
        result = PackageResult()
        turn = plan.turn or s.turn_count

        def newer(key: str) -> bool:
            if s.field_turns.get(key, 0) > turn:
                result.notes.append(f"{key} from turn {turn} ignored: turn {s.field_turns[key]} already set it")
                return True
            return False
        if pkg is None:
            result.notes.append("no package (reply was not valid JSON); record unchanged")
            return result
        if s.phase is Phase.CRISIS:
            result.notes.append("in CRISIS: package ignored")
            return result

        learned = dict(pkg.learned)
        if "timing" in learned and plan.asked != "timing":
            # Only an answer to the timing question sets it (not a "yeah" to the reflection).
            result.notes.append(f"timing {learned.pop('timing')!r} ignored: not an answer to the timing question")
        email = learned.pop("email", None)
        if email and not s.has("email") and plan.email_confirmed:
            local = email.split("@")[0].lower()
            if local == plan.email_confirmed and "@" in email:
                learned["email"] = email.lower()
            else:
                result.notes.append(f"email {email!r} ignored: doesn't match spelled "
                                    f"{plan.email_confirmed!r}")
        elif email and not s.has("email"):
            result.notes.append(f"email {email!r} ignored: not confirmed yet")

        if plan.asked == "school" and "school" not in learned and not s.has("school"):
            # Short answers ("Rice") sometimes go unrecorded; take the caller's words.
            school = planner.school_from_text(plan.caller_text)
            if school:
                learned["school"] = school
                result.notes.append(f"school {school!r} taken from the caller's words")
        for name, value in list(learned.items()):
            if newer(name):
                del learned[name]
                continue
            s.field_turns[name] = turn
            if s.profile.get(name) != value:
                s.profile[name] = value
                result.changed = True
        if turn >= s.turn_count:
            # Only the latest turn's package says what the caller's latest line answered.
            s.last_learned = set(learned) | set(plan.profile)

        for name, value in pkg.confidence.items():
            old = s.confidence.get(name, 0.0)
            if value == 0.0 and old > 0.0 and s.has(name):
                continue   # a bare 0 for a known field is the template default, not a judgment
            if newer(f"conf:{name}"):
                continue
            s.field_turns[f"conf:{name}"] = turn
            if value != old:
                s.confidence[name] = value
                result.changed = True

        if pkg.summary and learned:
            leak = self._identity_in(pkg.summary)
            if leak:
                result.notes.append(f"summary rejected: contains {leak!r}")
            elif not newer("summary"):
                s.field_turns["summary"] = turn
                if pkg.summary != s.summary:
                    s.summary = pkg.summary
                    result.changed = True

        if pkg.crisis_level == "urgent":
            s.raise_crisis("urgent", pkg.crisis_reason or "model: urgent")
            s.set_phase(Phase.CRISIS)
            result.crisis_now = result.changed = True
            result.notes.append(f"model crisis=urgent ({pkg.crisis_reason}) -> CRISIS")
        elif pkg.crisis_level == "concern":
            if s.raise_crisis("concern", pkg.crisis_reason or "model: concern"):
                result.changed = True
            if not s.safety_pending:
                s.safety_needed = pkg.crisis_reason or "model: concern"
                result.notes.append(f"model crisis=concern ({pkg.crisis_reason}): safety question next")

        if pkg.move and pkg.move == plan.alternative and turn >= s.turn_count and s.moves:
            # The goal let the model take an early exit (or it improvised): record what it did.
            result.notes.append(f"model made move {pkg.move} instead of {plan.move}")
            result.move = pkg.move
            s.moves[-1] = pkg.move
            if pkg.move == WRAP and s.phase is Phase.DEEPEN:
                s.set_phase(Phase.REFLECT)
                s.branch = (None, "reflect")
            result.changed = True

        if s.phase is Phase.TIMING:
            nxt = planner.advance(s)
            if nxt is Phase.CLOSING:
                s.set_phase(Phase.CLOSING)
                result.closing_now = result.changed = True
            elif nxt is Phase.EMAIL:
                s.set_phase(Phase.EMAIL)
                result.changed = True

        if pkg.ready_for_next_phase:
            result.notes.append("model hint: ready_for_next_phase")
        if result.changed:
            self._changed()
        return result

    def _identity_in(self, summary: str) -> str | None:
        low = summary.lower()
        for name in IDENTITY_FIELDS:
            value = self.state.profile.get(name)
            if isinstance(value, str) and len(value) >= 2 and \
                    re.search(rf"\b{re.escape(value.lower())}\b", low):
                return value
        return None

    # ---- operator lines, silence, end ---------------------------------------

    def operator_said(self, text: str, ts: float | None = None) -> None:
        """Record what Aide actually said (the played part, after a barge-in)."""
        s = self.state
        text = " ".join((text or "").split())
        if not text:
            return
        s.add_line("operator", text, ts)
        letters = planner.spelled_letters(text)
        if letters and s.phase is Phase.EMAIL and not planner.email_done(s):
            s.email_spelled = letters
            s.email_spelled_domain = planner.spelled_domain(text)
        self._changed()

    def caller_present(self) -> None:
        """The caller said something that isn't a turn (an ignored "yeah"): they're still
        there, so the silence reminders start over."""
        self.state.silence_count = 0

    def on_silence(self) -> TurnPlan:
        s = self.state
        s.silence_count += 1
        p = TurnPlan(caller_text="", phase=s.phase)
        if s.phase is Phase.CRISIS:
            p.fixed, p.end_call = "crisis_closing", True
        elif s.silence_count == 1:
            p.fixed = "reminder_1"
        elif s.silence_count == 2:
            p.fixed = "reminder_2"
        else:
            p.fixed, p.end_call = "closing", True
            if s.phase is not Phase.CLOSING:
                s.set_phase(Phase.CLOSING)
                self._changed()
        return p

    def end(self, ts: float | None = None) -> None:
        if self.state.ended_ts is None:
            self.state.ended_ts = ts or time.time()
            self._changed()
