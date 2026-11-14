"""Make clean text look like speech-recognition output.

Applied AFTER redaction + normalize_for_clf, so it only needs the errors Whisper
actually makes on phone audio: dropped words, homophones, numbers spelled out,
fillers, and split/merged words. Deterministic given a seed.
"""
from __future__ import annotations

import random

HOMOPHONES = {
    "their": ["there", "they're"], "there": ["their"], "to": ["two", "too"], "for": ["four"],
    "your": ["you're"], "you're": ["your"], "its": ["it's"], "know": ["no"], "right": ["write"],
    "buy": ["by", "bye"], "by": ["buy"], "here": ["hear"], "won't": ["want"], "cards": ["cars"],
    "visa": ["visit", "visor"], "irs": ["i r s", "ira"], "uscis": ["u s c i s", "you see this"],
    "sevis": ["seavis", "seville"], "zelle": ["zell", "sell"], "bitcoin": ["bit coin"],
    "warrant": ["warren"], "deported": ["reported"], "fee": ["free"], "check": ["chuck"],
    "wire": ["wider", "why are"], "gift": ["give"], "cvs": ["c v s"], "atm": ["a t m"],
}
NUM_WORDS = {"600": "six hundred", "850": "eight hundred fifty", "1200": "twelve hundred",
             "2400": "twenty four hundred", "450": "four hundred fifty", "3000": "three thousand",
             "975": "nine hundred seventy five", "1500": "fifteen hundred", "42": "forty two"}
FILLERS = ["um", "uh", "like", "you know", "so"]


def asr_noise(text: str, seed: int | None = None, p_drop: float = 0.05, p_homo: float = 0.10,
              p_filler: float = 0.03, p_num: float = 0.5) -> str:
    r = random.Random(seed)
    out: list[str] = []
    for w in text.split():
        if w.startswith("["):  # redaction tags are produced by our code, not ASR
            out.append(w)
            continue
        if r.random() < p_drop:
            continue
        if w in NUM_WORDS and r.random() < p_num:
            out.extend(NUM_WORDS[w].split())
            continue
        if w in HOMOPHONES and r.random() < p_homo:
            out.extend(r.choice(HOMOPHONES[w]).split())
        else:
            out.append(w)
        if r.random() < p_filler:
            out.append(r.choice(FILLERS))
    return " ".join(out)
