"""Risk engine (section 8): features -> raw score -> Marlowe-style stabilizer ->
GREEN / YELLOW / RED, after every redacted utterance.

  raw_score   weighted mean of the AVAILABLE features (the LLM is optional),
              plus bonuses for a synthetic voice and for the student complying
  Shield      dead zone, confidence-weighted steps, up faster than down,
              faster across band boundaries, strong-evidence jump to RED,
              RED latched for 60 s, the "I'm suspicious" button as a floor

The color never waits on the LLM: if it errors or hasn't run, llm=None and the
classifier + keywords carry the score.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from typing import TYPE_CHECKING, Callable, Optional

from shared.contracts import RiskEvent
from shared.labels import PRETTY, SCAM_LABELS

from .coach import Coach
from .keywords import comply_score, kw_hits, kw_score

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Runtime
    from .session import CallSession

log = logging.getLogger("guardian.risk")

W = {"clf": 0.60, "kw": 0.25, "llm": 0.15}
LEVELS = ["GREEN", "YELLOW", "RED"]


def raw_score(f: dict) -> float:
    """f: clf, kw, llm (None until it runs), voice, comply -> 0..100."""
    avail = {k: v for k, v in f.items() if k in W and v is not None}
    base = sum(W[k] * v for k, v in avail.items()) / sum(W[k] for k in avail) if avail else 0.0
    return min(100.0, 100 * base + 10 * (f.get("voice") or 0) + 10 * (f.get("comply") or 0))


def band(s: float) -> int:
    return 0 if s < 40 else 1 if s < 70 else 2


class Shield:
    def __init__(self, *, red_latch_s: float = 60.0, down_after_s: float = 30.0, jump_raw: float = 85.0):
        self.s = 0.0
        self.level = 0
        self.red_at = 0.0
        self.up_at = 0.0
        self.red_latch_s = red_latch_s
        self.down_after_s = down_after_s
        self.jump_raw = jump_raw

    def update(self, raw: float, conf: float, now: float, flagged: bool = False) -> tuple[str, float]:
        d = raw - self.s
        if abs(d) > 3:  # dead zone: no cosmetic flicker
            cap = (11 if conf >= 0.7 else 6) * (1.5 if d > 0 else 1.0) * (2 if band(raw) != band(self.s) else 1)
            self.s += max(-cap, min(cap, d))
        if raw >= self.jump_raw:  # strong evidence -> RED now
            self.s = max(self.s, 70.0)
        want = band(self.s)
        if want > self.level:
            self.level, self.up_at = want, now
            if want == 2:
                self.red_at = now
        elif (want < self.level and now - self.up_at > self.down_after_s
              and not (self.level == 2 and now - self.red_at < self.red_latch_s)):
            self.level -= 1  # one step at a time
            self.up_at = now
        if flagged and self.level < 1:  # "I'm suspicious" button: at least YELLOW
            self.level, self.up_at = 1, now
        return LEVELS[self.level], self.s


class CallRisk:
    """Risk state for one call. Created lazily by RiskService on the first utterance."""

    def __init__(self, short_window_words: int = 6, drop: frozenset[str] = frozenset()):
        self.shield = Shield()
        self.drop = drop  # ablation: features forced to "unavailable"
        self.short = short_window_words
        self.llm_score: Optional[float] = None  # section 11 sets this
        self.llm_verdict = None  # last LLMVerdict
        self.coach = Coach()  # section 10
        self.last_second_opinion = -1e9
        self.voice_flag: float = 0.0  # section 13 sets this
        self.type_votes: Counter = Counter()
        self.red_flags: list[str] = []
        self.history: list[dict] = []
        self.last: Optional[RiskEvent] = None
        self.max_level = "GREEN"

    def features(self, probs: dict[str, float], norm: str) -> tuple[dict, list[str]]:
        p_scam = 1.0 - probs.get("benign", 0.0)
        words = len(norm.split())
        clf = p_scam * min(1.0, words / self.short)  # "okay thanks" carries little evidence
        hits = kw_hits(norm)
        f = {"clf": round(clf, 4), "p_scam": round(p_scam, 4), "kw": round(kw_score(hits), 4),
             "llm": self.llm_score, "voice": self.voice_flag, "comply": comply_score(norm)}
        for k in self.drop:
            f[k] = None
        return f, [h for h, _ in hits]

    def scam_type(self, probs: dict[str, float]) -> str:
        for l in SCAM_LABELS:  # accumulate evidence so the type doesn't flip-flop
            self.type_votes[l] += probs.get(l, 0.0)
        top, votes = self.type_votes.most_common(1)[0]
        return top if votes >= 0.5 else "benign"

    def step(self, probs: dict[str, float], norm: str, now: float, flagged: bool = False) -> dict:
        """One utterance: features -> raw -> stabilized level. Shared by the live
        RiskService and the offline call-level evaluation (ml/eval_calls.py)."""
        f, hits = self.features(probs, norm)
        raw = raw_score(f)
        conf = max(probs.values()) if probs else 0.0
        level, s = self.shield.update(raw, conf, now, flagged)
        stype = self.scam_type(probs) if level != "GREEN" or f["p_scam"] >= 0.5 else "benign"
        for h in hits:
            if h not in self.red_flags:
                self.red_flags.append(h)
        if LEVELS.index(level) > LEVELS.index(self.max_level):
            self.max_level = level
        return {"level": level, "score": s, "raw": raw, "features": f, "scam_type": stype}


class RiskService:
    """Registered as a transcript hook: runs after every redacted utterance."""

    def __init__(self, rt: "Runtime", clock: Callable[[], float] = time.time):
        self.rt = rt
        self.clock = clock
        self.calls: dict[str, CallRisk] = {}

    def state(self, session: "CallSession") -> CallRisk:
        if session.id not in self.calls:
            self.calls[session.id] = CallRisk()
        return self.calls[session.id]

    def flagged(self, user_id: str) -> bool:
        return self.rt.flags.get(user_id, 0.0) > self.clock()

    async def on_transcript(self, session: "CallSession", ev: dict) -> None:
        cr = self.state(session)
        window = session.recent_text(3)  # redacted
        probs, clf_ms, norm = await self.rt.clf.predict(window)
        now = self.clock()
        r = cr.step(probs, norm, now, self.flagged(session.user.id))
        card, advanced = cr.coach.update(r["level"], r["scam_type"], now, cr.llm_verdict)
        risk = RiskEvent(
            call_id=session.id, user_id=session.user.id, ts=now, level=r["level"], score=round(r["score"], 1),
            scam_type=r["scam_type"], red_flags=cr.red_flags[-6:], features={**r["features"], "raw": round(r["raw"], 1)},
            coach=card.public() if card else None,
            latency={"asr_ms": ev["latency"]["asr_ms"], "clf_ms": round(clf_ms, 2),
                     "llm_ms": round(cr.llm_verdict.ms) if cr.llm_verdict else None, "voice_ms": None},
            source="call",
        )
        await self._emit(session, cr, risk, utterance=len(session.windows))
        if advanced and card:
            await self.rt.hub.publish(session.user.id, "coach", call_id=session.id, **card.public())
            self.rt.alerts.coach_step(session.user, card.public())
        if r["level"] == "RED":
            self.rt.alerts.red(session.user, session.id, r["scam_type"])
        # section 11: async LLM verdict on the redacted last 90 s (never blocks the color)
        context = session.recent_seconds(90)
        self.rt.llm.request(session.id, r["level"], context, lambda cid, v: self._on_llm(session, v, context))
        if not self.rt.llm.enabled and r["level"] != "GREEN" and 0.4 <= r["features"]["p_scam"] <= 0.75:
            self._second_opinion(session, cr, None, r["features"]["p_scam"], r["scam_type"], context)

    async def _on_llm(self, session: "CallSession", v, context: str) -> None:
        cr = self.calls.get(session.id)
        if cr is None or cr.last is None:
            return  # call already ended
        cr.llm_score, cr.llm_verdict = v.score, v
        for q in v.red_flags:
            if q not in cr.red_flags:
                cr.red_flags.append(q)
        p_scam = (cr.last.features or {}).get("p_scam", 0.0)
        await self.rt.hub.publish(session.user.id, "llm", call_id=session.id, **v.public(), local_clf=p_scam)
        card = cr.coach.refine(cr.last.scam_type, v)
        if card:
            await self.rt.hub.publish(session.user.id, "coach", call_id=session.id, **card.public())
        if 0.4 <= v.score <= 0.75 or abs(v.score - p_scam) > 0.3:
            self._second_opinion(session, cr, v.score, p_scam, cr.last.scam_type, context)

    def _second_opinion(self, session: "CallSession", cr: CallRisk, llm: Optional[float], clf: float,
                        scam_type: str, context: str) -> None:
        now = self.clock()
        if now - cr.last_second_opinion < 20 or not context:
            return
        cr.last_second_opinion = now
        self.rt.cloud.send_second_opinion({"user_id": session.user.id, "call_id": session.id, "local_llm": llm,
                                           "local_clf": round(clf, 3), "scam_type": scam_type, "context": context})

    async def on_flag(self, user_id: str) -> None:
        """'I'm suspicious' pressed: raise the active call to at least YELLOW now."""
        session = self.rt.sessions.active(user_id)
        if not session or session.id not in self.calls:
            return
        cr = self.calls[session.id]
        level, s = cr.shield.update(cr.shield.s, 1.0, self.clock(), flagged=True)
        if cr.last:
            risk = cr.last.model_copy(update={"level": level, "score": round(s, 1), "ts": self.clock(), "source": "user_flag"})
            await self._emit(session, cr, risk, utterance=len(session.windows))

    async def on_end(self, session: "CallSession") -> None:
        cr = self.calls.pop(session.id, None)
        self.rt.llm.forget(session.id)
        if cr:
            await self.rt.hub.publish(session.user.id, "summary", call_id=session.id, max_level=cr.max_level,
                                      scam_type=cr.last.scam_type if cr.last else "benign",
                                      scam_label=PRETTY.get(cr.last.scam_type, "") if cr.last else "",
                                      red_flags=cr.red_flags[-6:], coach_steps=cr.coach.step,
                                      llm_score=cr.llm_score, history=cr.history[-60:])
        self.rt.cloud.send_session_end(session.user.id, session.id, cr.max_level if cr else "GREEN")

    async def _emit(self, session: "CallSession", cr: CallRisk, risk: RiskEvent, utterance: int) -> None:
        if LEVELS.index(risk.level) > LEVELS.index(cr.max_level):
            cr.max_level = risk.level  # (on_flag path)
        changed = cr.last is None or cr.last.level != risk.level
        cr.last = risk
        cr.history.append({"utterance": utterance, "level": risk.level, "score": risk.score})
        await self.rt.hub.publish(session.user.id, "risk", **risk.model_dump(exclude={"user_id", "ts"}),
                                  scam_label=PRETTY.get(risk.scam_type, risk.scam_type),
                                  utterance=utterance, changed=changed, max_level=cr.max_level)
        if changed:
            log.info("[%s] %s -> %s (score %.0f, %s)", session.user.id, cr.history[-2]["level"] if len(cr.history) > 1 else "-",
                     risk.level, risk.score, risk.scam_type)
        self.rt.cloud.send_risk(risk)  # fire-and-forget: the color never waits on the network

