"""Section 11 (cloud half): Gemini second opinion on borderline calls.

The edge asks only when the local score is 0.4-0.75 or the local LLM and the
classifier disagree by > 0.3, and sends only the REDACTED last 90 s. Shown on
/ops as "Local 0.62 · Gemini 0.81". Display only: the firewall uses the edge's
level, so a slow or offline Gemini never changes what gets held.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import httpx

from shared.labels import LABELS

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PROMPT = """You review a phone call transcript for a scam-protection app for US college students.
The transcript is from speech recognition; personal data is redacted as [NAME], [PHONE], etc.
Labels: {labels}.
Return JSON: {{"score": probability 0-1 that it is a scam, "scam_type": one label,
"red_flags": up to 4 exact short quotes from the transcript, "reason": one sentence}}.
Facts: the IRS, USCIS and universities never demand gift cards, crypto or wire transfers by phone; banks never ask
for one-time codes. Legitimate calls can be urgent without asking for money or secrets.

Transcript:
{transcript}"""


class Gemini:
    def __init__(self, api_key: str, model: str, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.key = api_key
        self.model = model
        self._http = httpx.AsyncClient(timeout=20, transport=transport)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    async def opinion(self, transcript: str) -> Optional[dict]:
        if not self.key:
            return None
        t0 = time.perf_counter()
        body = {"contents": [{"parts": [{"text": PROMPT.format(labels=", ".join(LABELS), transcript=transcript[:6000])}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
        r = await self._http.post(API.format(model=self.model), json=body, headers={"x-goog-api-key": self.key})
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        d = json.loads(text)
        score = min(1.0, max(0.0, float(d.get("score", 0))))
        t = transcript.lower()
        flags = [q for q in (d.get("red_flags") or []) if isinstance(q, str) and q.lower() in t][:4]
        return {"score": round(score, 3), "scam_type": d.get("scam_type") if d.get("scam_type") in LABELS else None,
                "red_flags": flags, "reason": str(d.get("reason", ""))[:300], "ms": round((time.perf_counter() - t0) * 1000)}

    async def close(self) -> None:
        await self._http.aclose()
