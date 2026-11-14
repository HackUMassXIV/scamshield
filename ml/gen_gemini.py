"""Generate realistic call transcripts with Gemini (more varied than templates).

  set GEMINI_API_KEY in .env, then:
  python -m ml.gen_gemini --per-label 80 --out data/ml/calls_gemini.jsonl

Uses the plain REST API (no SDK version issues). The model is configurable
because Gemini model names change: GEMINI_MODEL in .env or --model. Check
https://ai.google.dev/gemini-api/docs/models for the current flash model.
Resumable: calls already in --out are counted and skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

from shared.labels import LABELS, PRETTY

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SCAM_HINTS = {
    "irs_gov": "caller impersonates the IRS/Treasury/Social Security; back taxes, suspended SSN, arrest threats; demands gift cards, wire, crypto or SSN",
    "immigration_uscis": "caller impersonates USCIS/ICE/DHS/SEVIS; visa or F-1/OPT problem, deportation threat; demands a 'fee' via gift cards, wire or crypto",
    "tech_support": "caller impersonates Microsoft/Apple/antivirus; virus or hacked laptop; wants remote access (AnyDesk), bank login, or payment",
    "rental_deposit": "fake landlord for an apartment/sublet near campus; can't show it, 'others want it'; wants deposit via Zelle/wire/gift cards before viewing",
    "fake_job_check": "fake employer; mails a check, asks to deposit it and send the 'extra' back or buy equipment/gift cards; or asks for bank login",
    "family_emergency": "caller pretends to be a relative (or their lawyer/police) in jail/accident abroad; urgent bail or hospital money; 'don't tell mom'",
    "bank_impersonation": "caller impersonates the bank fraud team; asks for one-time code, PIN, card number, or to move money to a 'safe account'",
}
BENIGN_URGENT = [
    "a REAL bank fraud alert that only asks the student to check their app or call the number on their card, and says the bank never asks for PIN or codes",
    "the campus pharmacy saying a prescription is ready and insurance needs updating at the counter",
    "a landlord about a water leak and a plumber visit, nothing to pay",
    "the university international student office reminding the student to update their SEVIS address in the portal, no fee",
    "the campus volunteer tax clinic confirming a free appointment and listing documents to bring",
    "a store saying a gift card the student ordered for a friend has shipped",
    "the health center asking the student to call back about lab results",
    "a real employer (campus dining) scheduling an interview, nothing to pay",
]
BENIGN_NORMAL = ["a friend about homework", "a parent checking in", "a professor about office hours",
                 "a delivery driver at the door", "a roommate about chores and wifi", "a dentist appointment reminder"]

PROMPT = """Write {n} different, realistic phone call transcripts. Each one: {desc}.
The person called is a US college student{intl}. Vary names, amounts, tactics, tone, and length (8 to 20 turns).
Make the student react realistically (nervous, confused, skeptical or cooperative).
Do NOT include stage directions or narration. Write how people actually talk on the phone.
Return JSON: {{"calls": [{{"turns": [{{"speaker": "CALLER" or "STUDENT", "text": "..."}}]}}]}}"""


def describe(label: str, r: random.Random) -> tuple[str, str]:
    if label == "benign":
        if r.random() < 0.5:
            return "a legitimate call that sounds urgent: " + r.choice(BENIGN_URGENT), "urgent:gemini"
        return "an ordinary legitimate call: " + r.choice(BENIGN_NORMAL), "normal:gemini"
    return f"a {PRETTY[label]} scam: {SCAM_HINTS[label]}", "scam"


async def one_batch(client: httpx.AsyncClient, model: str, key: str, label: str, n: int, r: random.Random) -> list[dict]:
    desc, subtype = describe(label, r)
    intl = r.random() < 0.3
    prompt = PROMPT.format(n=n, desc=desc, intl=" who is an international student on an F-1 visa" if intl else "")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0}}
    for attempt in range(5):
        resp = await client.post(API.format(model=model), json=body, headers={"x-goog-api-key": key})
        if resp.status_code in (429, 500, 503):
            await asyncio.sleep(2 ** attempt + r.random())
            continue
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        try:
            calls = json.loads(text)["calls"]
        except (ValueError, KeyError, TypeError):
            return []
        out = []
        for c in calls:
            turns = [t for t in c.get("turns", []) if isinstance(t, dict) and t.get("text")]
            if len(turns) >= 4:
                out.append({"label": label, "subtype": subtype, "intl": intl, "source": "gemini", "turns": turns})
        return out
    return []


async def run(a) -> None:
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY", "")
    model = a.model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not key:
        raise SystemExit("set GEMINI_API_KEY in .env")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    have = Counter()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                have[json.loads(line)["label"]] += 1
    r = random.Random(a.seed + sum(have.values()))
    sem = asyncio.Semaphore(a.concurrency)
    lock = asyncio.Lock()

    async def worker(label: str) -> None:
        target = a.per_label * (2 if label == "benign" else 1)
        async with httpx.AsyncClient(timeout=120) as client:
            while have[label] < target:
                async with sem:
                    batch = await one_batch(client, model, key, label, min(a.batch, target - have[label]), r)
                async with lock:
                    with out.open("a", encoding="utf-8") as f:
                        for c in batch:
                            c["call_id"] = f"gem-{label}-{have[label]:04d}"
                            have[label] += 1
                            f.write(json.dumps(c) + "\n")
                print(f"{label}: {have[label]}/{target}", flush=True)
                if not batch:
                    await asyncio.sleep(2)

    await asyncio.gather(*(worker(l) for l in LABELS))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-label", type=int, default=80)
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--model", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="data/ml/calls_gemini.jsonl")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
