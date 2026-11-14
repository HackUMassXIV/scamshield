"""Section 10: live coaching.

Escalation (never repeats a step, one step at a time):
  step 1  when the call first reaches YELLOW
  step 2  after 30 s at YELLOW, or as soon as it reaches RED (also if it skipped YELLOW)
  step 3  after 20 s at RED  -> HANG_UP

`say` may come from the LLM (if under 20 words and its scam type matches the
classifier's); the `fact` ALWAYS comes from shared/playbook.json, so it can't be
wrong. Coaching is delivered only to the student (app card, SMS banner,
earbuds), never into the merged call where the scammer would hear it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.labels import PRETTY

from .llm import LLMVerdict

PLAYBOOK: dict[str, list[dict]] = {
    k: v for k, v in json.loads((Path(__file__).resolve().parent.parent / "shared" / "playbook.json").read_text()).items()
    if not k.startswith("_")
}
YELLOW_TO_STEP2_S = 30.0
RED_TO_STEP3_S = 20.0


@dataclass
class CoachCard:
    step: int
    scam_type: str
    say: str
    fact: str
    action: Optional[str]
    source: str  # "playbook" | "llm"
    do_now: Optional[str] = None

    def public(self) -> dict:
        return {"step": self.step, "scam_type": self.scam_type, "scam_label": PRETTY.get(self.scam_type, "Possible scam"),
                "say": self.say, "fact": self.fact, "action": self.action, "source": self.source, "do_now": self.do_now}


def llm_say_ok(v: Optional[LLMVerdict], scam_type: str) -> bool:
    return bool(v and v.say_next and len(v.say_next.split()) < 20 and v.scam_type == scam_type)


class Coach:
    def __init__(self):
        self.step = 0
        self.step_at = 0.0
        self.yellow_since: Optional[float] = None
        self.red_since: Optional[float] = None
        self.card: Optional[CoachCard] = None

    def update(self, level: str, scam_type: str, now: float, llm: Optional[LLMVerdict] = None) -> tuple[Optional[CoachCard], bool]:
        """Returns (current card, whether a NEW step was reached)."""
        if level in ("YELLOW", "RED") and self.yellow_since is None:
            self.yellow_since = now
        if level == "RED" and self.red_since is None:
            self.red_since = now
        target = self.step
        if self.step < 2 and level == "RED":
            target = 2  # RED begins: straight to step 2 (even if the call jumped GREEN -> RED)
        elif self.step == 0 and level == "YELLOW":
            target = 1
        elif self.step == 1 and level == "YELLOW" and now - self.step_at >= YELLOW_TO_STEP2_S:
            target = 2
        elif self.step == 2 and level == "RED" and self.red_since is not None and now - self.red_since >= RED_TO_STEP3_S:
            target = 3
        advanced = target != self.step
        if advanced:
            self.step, self.step_at = target, now
        if self.step:
            self.card = self._card(scam_type, llm)
        return self.card, advanced

    def refine(self, scam_type: str, llm: LLMVerdict) -> Optional[CoachCard]:
        """An LLM verdict arrived: rewrite the 'say' line of the current step if it fits."""
        if self.step and self.step < 3 and llm_say_ok(llm, scam_type):
            self.card = self._card(scam_type, llm)
            return self.card
        return None

    def _card(self, scam_type: str, llm: Optional[LLMVerdict]) -> CoachCard:
        steps = PLAYBOOK.get(scam_type) or PLAYBOOK["generic"]
        p = steps[self.step - 1]
        use_llm = self.step < 3 and llm_say_ok(llm, scam_type)  # the hang-up step stays scripted
        return CoachCard(step=self.step, scam_type=scam_type if scam_type in PLAYBOOK else "generic",
                         say=llm.say_next if use_llm else p["say"], fact=p["fact"], action=p.get("action"),
                         source="llm" if use_llm else "playbook", do_now=llm.do_now if use_llm else None)
