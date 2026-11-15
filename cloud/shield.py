"""Per-student shield state, fed by the edge's redacted RiskEvents.

This is what the payment firewall reads: "was this student on a scam call in
the last hour, how bad was it, and are they still on it?"
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from shared.contracts import RiskEvent

LEVELS = ["GREEN", "YELLOW", "RED"]


@dataclass
class ShieldState:
    user_id: str
    level: str = "GREEN"  # latest level from the edge
    scam_type: str = "benign"
    score: float = 0.0
    call_id: Optional[str] = None
    session_active: bool = False
    since: float = 0.0  # when the current level began
    last_event: float = 0.0
    last_red_at: float = 0.0  # last time the call was RED
    last_yellow_at: float = 0.0  # last time it was YELLOW or worse
    red_scam_type: str = "benign"
    red_call_id: Optional[str] = None  # the call that last went RED
    flag_until: float = 0.0  # "I'm suspicious" pressed
    red_flags: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return asdict(self)


class ShieldStore:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._s: dict[str, ShieldState] = {}

    def get(self, user_id: str) -> ShieldState:
        return self._s.setdefault(user_id, ShieldState(user_id=user_id))

    def update(self, ev: RiskEvent) -> ShieldState:
        st = self.get(ev.user_id)
        now = self.clock()
        if ev.call_id != st.call_id:  # a new call: show its own level/type, not the last call's
            st.since = now
            st.scam_type = "benign"
            st.red_flags = []
        elif ev.level != st.level:
            st.since = now
        st.level, st.score, st.call_id = ev.level, ev.score, ev.call_id
        st.session_active = True
        st.last_event = now
        if ev.scam_type != "benign":
            st.scam_type = ev.scam_type
        if ev.level == "RED":
            st.last_red_at = now
            st.red_scam_type = st.scam_type
            st.red_call_id = ev.call_id
        if ev.level in ("YELLOW", "RED"):
            st.last_yellow_at = now
        st.red_flags = list(ev.red_flags)[-6:]
        return st

    def end_session(self, user_id: str, call_id: str, max_level: str = "GREEN") -> ShieldState:
        st = self.get(user_id)
        if st.call_id == call_id:
            st.session_active = False
        # A later call that stayed GREEN the whole time clears the earlier scam
        # call's effect on payments (a safe call after a scam call is not flagged).
        # The scam call itself, or one that went YELLOW, keeps the window open.
        if max_level == "GREEN" and call_id != st.red_call_id and (st.last_red_at or st.last_yellow_at):
            st.last_red_at = st.last_yellow_at = 0.0
            st.red_call_id, st.red_scam_type, st.scam_type = None, "benign", "benign"
            st.level, st.score, st.red_flags = "GREEN", 0.0, []
        return st

    def flag(self, user_id: str, seconds: int) -> ShieldState:
        st = self.get(user_id)
        st.flag_until = max(st.flag_until, self.clock() + seconds)
        return st