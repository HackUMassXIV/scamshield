"""Keyword signals: instant, explainable, and the fallback when no model loads.

All patterns run on normalize_for_clf(redacted window): lowercase, no
punctuation, "$600" -> "600 dollars". A negation guard skips matches preceded
by "never"/"not"/"won't" (a real bank alert says "we will NEVER ask for your PIN").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shared.labels import LABELS


@dataclass(frozen=True)
class KW:
    pattern: str
    weight: float
    label: str | None = None  # scam type this phrase hints at
    negatable: bool = True


KEYWORDS: list[KW] = [
    # payment methods scammers use
    KW(r"(google play |apple |steam |itunes |target |ebay |visa )?gift ?cards?|google play( cards?)?|itunes cards?|steam cards?", 1.0),
    KW(r"bitcoin( atm)?|crypto(currency)?|usdt|bit coin", 1.0),
    KW(r"wire (transfer|the money|it)|western union|moneygram", 0.8),
    KW(r"\bzelle\b|\bvenmo\b|cash app", 0.4),
    KW(r"safe account|secure account", 1.0, "bank_impersonation"),
    KW(r"send (the )?difference back|deposit (the|this) check|overpaid|extra money back", 1.0, "fake_job_check"),
    # threats
    # threats are never negated: scammers phrase them as "if you do NOT pay, a warrant..."
    KW(r"\bwarrant\b|arrest(ed)?\b|\bjail\b|police (will|are)", 0.8, negatable=False),
    KW(r"deport(ed|ation)?|visa (will be )?(cancel+ed|revoked|terminated)|out of status", 0.9, "immigration_uscis", negatable=False),
    KW(r"lawsuit|legal action|final notice", 0.6, "irs_gov", negatable=False),
    KW(r"(account|accounts) (will be )?(frozen|locked|suspended)|social security number (has been )?suspended", 0.7, negatable=False),
    # secrecy and isolation
    KW(r"(don't|do not) (tell|discuss|talk)|keep (this|it) (secret|between us|confidential)|this is confidential", 1.0, negatable=False),
    KW(r"(don't|do not) hang up|stay on the (line|phone)", 0.8, negatable=False),
    KW(r"(don't|do not) (call|contact) (your|the|anyone|my)", 0.7, negatable=False),
    # credential and access grabs
    KW(r"one time code|verification code|your pin\b|online banking password|bank login|full card number", 0.9, "bank_impersonation"),
    KW(r"(confirm|verify|read me) your (full )?(social|ssn|passport)", 0.9),
    KW(r"anydesk|teamviewer|remote access|access your computer", 1.0, "tech_support"),
    # urgency (weak alone)
    KW(r"right now|immediately|within (the|an|two) hours?|in the next (ten|few) minutes|today or", 0.3),
]
_COMPILED = [(re.compile(k.pattern), k) for k in KEYWORDS]
_NEG = re.compile(r"\b(never|not|won't|wont|no need|don't need)\b(\s+\w+){0,6}\s*$")
_COND = re.compile(r"\b(if|unless|otherwise|or else)\b")  # "if you don't buy gift cards..." is not a negation

COMPLY = re.compile(
    r"how many (cards|gift cards)|where do i buy|i'm (at|going to) the store|i'm going to the store|"
    r"(read|give) you the (numbers|code)|okay i will send|i'll send (it|the money)|how do i send|"
    r"okay i won't tell|what's the code|i'll buy"
)


def kw_hits(norm_text: str) -> list[tuple[str, KW]]:
    """Matches as (matched text, keyword)."""
    out = []
    for rx, k in _COMPILED:
        for m in rx.finditer(norm_text):
            if k.negatable:
                before = norm_text[max(0, m.start() - 60) : m.start()]
                neg = _NEG.search(before)
                if neg and not _COND.search(before[: neg.start()]):
                    continue
            out.append((m.group(0), k))
            break  # one hit per pattern per window
    return out


def kw_score(hits: list[tuple[str, KW]]) -> float:
    return min(1.0, sum(k.weight for _, k in hits) / 3.0)


def comply_score(norm_text: str) -> float:
    return 1.0 if COMPLY.search(norm_text) else 0.0


def keyword_label_probs(norm_text: str) -> dict[str, float]:
    """Crude label distribution from keywords: the classifier of last resort."""
    hits = kw_hits(norm_text)
    s = kw_score(hits)
    probs = {l: 0.0 for l in LABELS}
    probs["benign"] = 1.0 - s
    hinted = [k.label for _, k in hits if k.label]
    if s > 0:
        if hinted:
            for l in hinted:
                probs[l] += s / len(hinted)
        else:
            scams = [l for l in LABELS if l != "benign"]
            for l in scams:
                probs[l] += s / len(scams)
    return probs
