"""Per-call record: phase, learned profile, confidences, summary, crisis, transcript."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Phase(str, Enum):
    OPENING = "OPENING"
    IDENTITY = "IDENTITY"
    CONTEXT = "CONTEXT"
    DEEPEN = "DEEPEN"
    REFLECT = "REFLECT"
    TIMING = "TIMING"
    EMAIL = "EMAIL"
    CLOSING = "CLOSING"
    CRISIS = "CRISIS"


# The normal flow, in order. CRISIS sits outside it.
PHASE_ORDER = [Phase.OPENING, Phase.IDENTITY, Phase.CONTEXT, Phase.DEEPEN,
               Phase.REFLECT, Phase.TIMING, Phase.EMAIL, Phase.CLOSING]

IDENTITY_FIELDS = ["name", "school", "year", "major", "email"]
PROFILE_FIELDS = IDENTITY_FIELDS + ["situation", "themes", "stage", "wants", "partner_traits",
                                    "ai_use", "interests", "timing"]
CONF_FIELDS = ["situation", "themes", "stage", "wants"]
LIST_FIELDS = {"themes", "interests", "partner_traits"}

THEMES = ["homesick", "loneliness", "breakup", "academic_pressure", "family_conflict",
          "friendship_issues", "roommate_issues", "identity", "first_gen", "financial_stress",
          "burnout", "social_anxiety", "grief", "career_uncertainty", "health"]
ENUMS = {
    "stage": ["days", "weeks", "months", "years"],
    "wants": ["vent", "advice", "been_through_it", "company"],
    "timing": ["now", "later", "either"],
}
CRISIS_LEVELS = ["none", "concern", "urgent"]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


@dataclass
class Line:
    speaker: str  # "caller" | "operator"
    text: str
    ts: float

    def export(self) -> dict[str, Any]:
        return {"speaker": self.speaker, "text": self.text, "t": iso(self.ts)}


@dataclass
class CallState:
    call_id: str
    caller_phone: str = ""
    started_ts: float = field(default_factory=time.time)
    ended_ts: float | None = None

    phase: Phase = Phase.OPENING
    phase_reached: Phase = Phase.OPENING
    profile: dict[str, Any] = field(default_factory=lambda: dict.fromkeys(PROFILE_FIELDS))
    confidence: dict[str, float] = field(default_factory=lambda: dict.fromkeys(CONF_FIELDS, 0.0))
    summary: str | None = None
    crisis_level: str = "none"
    crisis_reason: str = ""

    turn_count: int = 0                      # final caller turns
    attempts: dict[str, int] = field(default_factory=dict)  # goal field -> times asked
    transcript: list[Line] = field(default_factory=list)

    returning: bool = False                  # identity came from a saved record
    reflect_confirmed: bool = False
    safety_pending: bool = False             # last operator line was the safety question
    safety_needed: str = ""                  # reason to ask the safety question next turn
    email_spelled: str = ""                  # letters Aide last spelled back, awaiting a yes
    email_spelled_domain: str = ""           # domain Aide said in that spell-back, if any
    email_done: bool = False                 # confirmed or declined: never asked again
    identity_skipped: bool = False           # caller opened up during IDENTITY; rest asked at the end
    # Moves Aide made in CONTEXT/DEEPEN, in order (what the model reported doing).
    moves: list[str] = field(default_factory=list)
    probed: list[str] = field(default_factory=list)   # themes already asked about from the bank
    # The last goal's branches: (field to retry if the answer was vague, item to
    # ask if it was clear). Resolved against last_learned on the next turn.
    branch: tuple[str | None, str | None] = (None, None)
    last_learned: set[str] = field(default_factory=set)  # fields the latest package filled
    # Which turn last set each profile field ("conf:<field>" for confidences, "summary"),
    # so a late package from an older turn never overwrites a newer turn's value.
    field_turns: dict[str, int] = field(default_factory=dict)
    silence_count: int = 0

    latency: list[dict[str, Any]] = field(default_factory=list)

    # ---- helpers -----------------------------------------------------------

    def has(self, name: str) -> bool:
        value = self.profile.get(name)
        return value is not None and value != [] and value != ""

    def tries(self, name: str) -> int:
        return self.attempts.get(name, 0)

    def elapsed(self, now: float | None = None) -> float:
        return (now or time.time()) - self.started_ts

    def set_phase(self, phase: Phase) -> None:
        self.phase = phase
        if phase is Phase.CRISIS:
            self.phase_reached = Phase.CRISIS
        elif self.phase_reached is not Phase.CRISIS and \
                PHASE_ORDER.index(phase) > PHASE_ORDER.index(self.phase_reached):
            self.phase_reached = phase

    def raise_crisis(self, level: str, reason: str) -> bool:
        """Crisis level only goes up. Returns True if it changed."""
        if CRISIS_LEVELS.index(level) <= CRISIS_LEVELS.index(self.crisis_level):
            if level == self.crisis_level and reason and not self.crisis_reason:
                self.crisis_reason = reason
            return False
        self.crisis_level = level
        if reason:
            self.crisis_reason = reason
        return True

    def add_line(self, speaker: str, text: str, ts: float | None = None) -> Line:
        line = Line(speaker, text, ts or time.time())
        self.transcript.append(line)
        return line

    def last_operator_line(self) -> str:
        for line in reversed(self.transcript):
            if line.speaker == "operator":
                return line.text
        return ""

    @property
    def ready_to_match(self) -> bool:
        return self.reflect_confirmed and self.crisis_level == "none" \
            and self.phase_reached is not Phase.CRISIS

    # ---- export ------------------------------------------------------------

    def record(self) -> dict[str, Any]:
        """Everything but the transcript (sent to dashboards on every change)."""
        return {
            "call_id": self.call_id,
            "caller_phone": self.caller_phone,
            "started_at": iso(self.started_ts),
            "ended_at": iso(self.ended_ts) if self.ended_ts else None,
            "phase": self.phase.value,
            "phase_reached": self.phase_reached.value,
            "profile": {k: self.profile.get(k) for k in PROFILE_FIELDS},
            "confidence": {k: round(self.confidence.get(k, 0.0), 2) for k in CONF_FIELDS},
            "summary": self.summary,
            "crisis": {"level": self.crisis_level, "reason": self.crisis_reason},
            "ready_to_match": self.ready_to_match,
        }

    def export(self) -> dict[str, Any]:
        """The record.json shape for the matching / embeddings side."""
        out = self.record()
        out.pop("phase")
        out["transcript"] = [line.export() for line in self.transcript]
        return out
