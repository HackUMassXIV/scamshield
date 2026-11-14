"""Shared data contracts: edge <-> cloud <-> app.

This file is the single source of truth. If you change a field here, change it
in `.cursor/rules/scamshield.mdc` too so Cursor keeps generating compatible code.

PRIVACY INVARIANT: nothing in these models may ever carry raw call audio or
un-redacted transcript text. Text fields hold *redacted* text only.
"""
from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

Level = Literal["GREEN", "YELLOW", "RED"]
RiskSource = Literal["call", "message", "user_flag"]
HoldStatus = Literal["HELD", "RELEASED", "CANCELLED", "EXPIRED"]
Speaker = Literal["mixed", "caller", "student"]


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


class RiskEvent(BaseModel):
    """Edge -> cloud. Emitted by the risk engine (section 8)."""

    call_id: str
    user_id: str
    ts: float = Field(default_factory=time.time)
    level: Level
    score: float  # 0-100 after stabilization
    scam_type: str
    red_flags: list[str] = Field(default_factory=list)  # redacted quotes
    features: dict = Field(default_factory=dict)  # clf, kw, llm, voice, comply
    coach: Optional[dict] = None  # {say, fact, action}
    latency: dict = Field(default_factory=dict)  # asr/clf/llm/voice ms
    source: RiskSource = "call"


class Transfer(BaseModel):
    """App -> cloud firewall (section 4)."""

    user_id: str
    payee_id: str
    amount: float
    memo: str = ""


class Hold(BaseModel):
    """Cloud firewall decision persisted in Mongo (section 4)."""

    id: str = Field(default_factory=lambda: new_id("hold_"))
    transfer: Transfer
    reasons: list[str] = Field(default_factory=list)
    points: float = 0.0
    status: HoldStatus = "HELD"
    created: float = Field(default_factory=time.time)
    release_after: float = 0.0


class TranscriptEvent(BaseModel):
    """Edge -> app websocket. One redacted utterance."""

    type: Literal["transcript"] = "transcript"
    call_id: str
    user_id: str
    speaker: Speaker = "mixed"
    text: str  # REDACTED
    redactions: dict[str, int] = Field(default_factory=dict)
    t_start: float
    t_end: float
    latency: dict = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


class StartBody(BaseModel):
    """PWA -> edge: "I've merged, start screening"."""

    user_id: str


class UserRecord(BaseModel):
    id: str
    name: str
    phone: str  # E.164, the caller-ID of the Guardian leg
    trusted_contact: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)


# Event `type` values pushed to the app over /ws/ui (all carry user_id, ts, call_id):
#   leg_connected        Guardian leg answered; tell student to Merge, then press Start
#   screening_requested  server asked Twilio to play the disclosure + open the stream
#   screening_started    media stream is live (mode: "merge" | "bridge")
#   transcript           a redacted utterance (TranscriptEvent fields)
#   risk                 RiskEvent fields + scam_label, utterance, changed, max_level
#   user_flag            student pressed "I'm suspicious" (until = epoch seconds)
#   coach                new coaching step: step, say, fact, action (HANG_UP at 3), source
#   llm                  local LLM verdict: score, scam_type, red_flags, say_next, do_now, ms
#   summary              after the call: max_level, scam_type, red_flags, coach_steps, history
#   leg_timeout          student never pressed Start; leg was hung up
#   call_ended           summary (utterances, audio seconds, ASR latency, drops)
UI_EVENT_TYPES = (
    "leg_connected",
    "screening_requested",
    "screening_started",
    "transcript",
    "risk",
    "user_flag",
    "coach",
    "llm",
    "summary",
    "leg_timeout",
    "call_ended",
)
