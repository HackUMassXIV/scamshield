"""Section 11: local LLM verdict via Ollama (runs on the laptop, works offline).

Rules from the plan:
  - only at YELLOW or higher, at most once every LLM_MIN_INTERVAL s (15), and a new
    request cancels the one still in flight
  - structured output: {score, scam_type, red_flags[], say_next, do_now}
  - temperature 0.1, num_predict 200, keep_alive 30m (warm-up request at startup)
  - short prompt, 3 few-shot examples, hard facts
  - anti-hallucination: drop any red_flags quote that is not in the transcript
The color never waits on this: results arrive asynchronously and feed the NEXT
risk update (CallRisk.llm_score), the coach, and the cloud's Gemini second opinion.

  ollama pull qwen2.5:3b        (qwen2.5:1.5b on a slow CPU)
  OLLAMA_KEEP_ALIVE=30m ollama serve
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import httpx

from shared.labels import LABELS

if TYPE_CHECKING:  # pragma: no cover
    from .config import Settings

log = logging.getLogger("guardian.llm")

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "scam_type": {"type": "string", "enum": LABELS},
        "red_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "say_next": {"type": "string"},
        "do_now": {"type": "string"},
    },
    "required": ["score", "scam_type", "red_flags", "say_next", "do_now"],
}

SYSTEM = """You screen live phone calls for scams targeting US college students.
The transcript comes from speech recognition: expect typos. Personal data is redacted as [NAME], [PHONE], etc.
Return JSON only.
score: probability 0-1 that this is a scam. scam_type: one of the given labels ("benign" if not a scam).
red_flags: up to 4 EXACT short quotes copied from the transcript. Never invent quotes.
say_next: one short sentence (under 20 words) the student can say right now. do_now: one short action.
Facts: the IRS, USCIS, ICE and universities never demand gift cards, crypto or wire transfers by phone.
Real banks never ask for one-time codes, PINs, or to move money to a "safe account".
Legitimate callers can be urgent (a real fraud alert, a pharmacy, a landlord) without asking for money or secrets."""

FEWSHOT = [
    ("this is officer [NAME] from uscis your visa is suspended you will be deported tonight unless you pay "
     "the 600 dollar fee in google play gift cards do not tell anyone stay on the line",
     {"score": 0.97, "scam_type": "immigration_uscis",
      "red_flags": ["you will be deported tonight", "google play gift cards", "do not tell anyone"],
      "say_next": "I will contact my university's international office before paying anything.",
      "do_now": "Hang up and check your case only at uscis.gov."}),
    ("hi this is an automated fraud alert from your bank we saw a 42 dollar purchase at a grocery store if this "
     "was not you call the number on the back of your card we will never ask for your pin",
     {"score": 0.06, "scam_type": "benign", "red_flags": [],
      "say_next": "Thanks, I'll check it in my banking app.",
      "do_now": "If unsure, call the number on the back of your card."}),
    ("hey it's me are you coming to the library tonight bring your notes from lecture i'll grab coffee",
     {"score": 0.02, "scam_type": "benign", "red_flags": [], "say_next": "Sure, see you there.", "do_now": "Nothing needed."}),
]


@dataclass
class LLMVerdict:
    score: float
    scam_type: str
    red_flags: list[str] = field(default_factory=list)
    say_next: str = ""
    do_now: str = ""
    ms: float = 0.0
    dropped_flags: int = 0  # quotes removed by the anti-hallucination filter

    def public(self) -> dict:
        return asdict(self)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$\[\]' ]+", " ", s.lower())).strip()


def parse_verdict(content: str, transcript: str, ms: float = 0.0) -> Optional[LLMVerdict]:
    """Validate the model's JSON and keep only quotes that really occur in the transcript."""
    try:
        d = json.loads(content)
        score = float(d["score"])
    except (ValueError, KeyError, TypeError):
        return None
    if score > 1.0 and score <= 100:  # some small models answer in percent
        score /= 100.0
    score = min(1.0, max(0.0, score))
    stype = d.get("scam_type") if d.get("scam_type") in LABELS else ("benign" if score < 0.5 else "irs_gov")
    t = _norm(transcript)
    flags, dropped = [], 0
    for q in d.get("red_flags") or []:
        nq = _norm(str(q))
        if nq and len(nq) >= 4 and nq in t:
            flags.append(nq)
        else:
            dropped += 1
    return LLMVerdict(score=score, scam_type=stype, red_flags=flags[:4],
                      say_next=str(d.get("say_next") or "").strip()[:200],
                      do_now=str(d.get("do_now") or "").strip()[:200], ms=ms, dropped_flags=dropped)


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float = 20.0, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.url = url.rstrip("/")
        self.model = model
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    def messages(self, transcript: str) -> list[dict]:
        msgs = [{"role": "system", "content": SYSTEM}]
        for text, out in FEWSHOT:
            msgs += [{"role": "user", "content": text}, {"role": "assistant", "content": json.dumps(out)}]
        return msgs + [{"role": "user", "content": transcript}]

    async def verdict(self, transcript: str) -> Optional[LLMVerdict]:
        t0 = time.perf_counter()
        r = await self._http.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False, "format": SCHEMA, "keep_alive": "30m",
            "options": {"temperature": 0.1, "num_predict": 200},
            "messages": self.messages(transcript)})
        r.raise_for_status()
        content = (r.json().get("message") or {}).get("content", "")
        return parse_verdict(content, transcript, (time.perf_counter() - t0) * 1000)

    async def warmup(self) -> bool:
        try:
            await self._http.post(f"{self.url}/api/chat", json={
                "model": self.model, "stream": False, "keep_alive": "30m", "options": {"num_predict": 1},
                "messages": [{"role": "user", "content": "hi"}]}, timeout=120)
            return True
        except Exception as exc:
            log.warning("Ollama not reachable at %s (%s): running without the LLM", self.url, exc)
            return False

    async def close(self) -> None:
        await self._http.aclose()


OnVerdict = Callable[[str, LLMVerdict], Awaitable[None]]


class LLMService:
    """Throttle + cancel-in-flight per call. `request()` never blocks the caller."""

    def __init__(self, client: Optional[OllamaClient], min_interval: float = 15.0, clock=time.monotonic):
        self.client = client
        self.min_interval = min_interval
        self.clock = clock
        self._last: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._backoff_until = 0.0
        self.calls = 0
        self.errors = 0

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def request(self, call_id: str, level: str, transcript: str, on_verdict: OnVerdict) -> bool:
        if not self.client or level == "GREEN" or not transcript.strip():
            return False
        now = self.clock()
        if now < self._backoff_until or now - self._last.get(call_id, -1e9) < self.min_interval:
            return False
        self._last[call_id] = now
        old = self._inflight.pop(call_id, None)
        if old and not old.done():
            old.cancel()  # a newer window supersedes it
        self._inflight[call_id] = asyncio.create_task(self._run(call_id, transcript, on_verdict))
        return True

    async def _run(self, call_id: str, transcript: str, on_verdict: OnVerdict) -> None:
        try:
            self.calls += 1
            v = await self.client.verdict(transcript)  # type: ignore[union-attr]
            if v:
                await on_verdict(call_id, v)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors += 1
            self._backoff_until = self.clock() + 30  # Ollama down: don't hammer it
            log.warning("LLM verdict failed (%s); classifier + keywords carry on", exc)

    def forget(self, call_id: str) -> None:
        self._last.pop(call_id, None)
        t = self._inflight.pop(call_id, None)
        if t and not t.done():
            t.cancel()

    async def close(self) -> None:
        for t in self._inflight.values():
            t.cancel()
        if self.client:
            await self.client.close()


def make_llm(s: "Settings") -> LLMService:
    client = OllamaClient(s.ollama_url, s.ollama_model, s.llm_timeout) if s.llm_backend == "ollama" else None
    return LLMService(client, s.llm_min_interval)
