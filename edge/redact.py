"""PII redaction. Runs on the edge, on every transcript, BEFORE the text is
stored, logged, pushed to the UI or sent to the cloud.

Covers: emails, card numbers (Luhn-checked) and other long digit runs, SSNs,
US phone numbers, spoken digit strings ("one two three four five six"), the
student's own name (from users.json), and names introduced by common cues
("my name is ...", "this is Officer ...", "Hello ..."). It is rule-based, so
treat it as a strong first line of defense, not a guarantee. Street addresses
are not covered.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")  # 13-19 digits
_SSN = re.compile(r"(?<!\d)\d{3}[- ]?\d{2}[- ]?\d{4}(?!\d)")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)")
_ACCOUNT = re.compile(r"(?<!\d)\d{8,12}(?!\d)")

_DIGIT_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_SPOKEN_DIGITS = re.compile(rf"\b{_DIGIT_WORDS}(?:[\s,.-]+{_DIGIT_WORDS}\b){{5,}}", re.IGNORECASE)

_TITLE = r"(?:officer|agent|special agent|detective|sergeant|inspector|mr|mrs|ms|miss|dr|mister)\.?"
_NAME_CUE = re.compile(
    r"(?i:\b(?:my name is|my name's|this is|i am|i'm|name is|speaking (?:to|with)|"
    r"am i speaking (?:to|with)|ask for|hello|hi|hey|dear)\s+(?:" + _TITLE + r"\s+)?)"
    r"(?P<name>[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})?)"
)
_TITLED_NAME = re.compile(
    r"(?i:\b" + _TITLE + r"\s+)(?P<name>[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})?)"
)
# Capitalized words that follow the cues but are organizations / common words.
# Keeping these readable matters: the classifier needs "this is the IRS".
_NOT_NAMES = {
    "irs", "uscis", "ice", "fbi", "dhs", "sevis", "immigration", "department", "social",
    "security", "bank", "capital", "chase", "wells", "fargo", "amazon", "apple", "microsoft",
    "google", "paypal", "venmo", "zelle", "walmart", "target", "umass", "university",
    "federal", "police", "customer", "support", "service", "services", "medicare", "visa",
    "mastercard", "the", "calling", "from", "with", "your", "tax", "revenue", "internal",
    "treasury", "court", "county", "state", "office", "student", "international", "hello",
    "okay", "yes", "sorry", "sure", "thanks", "thank", "please", "listen", "again", "there",
    "here", "now", "just", "not", "officer", "agent",
}


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) < 13:
        return False
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class Redactor:
    def __init__(self, extra_names: Iterable[str] = ()):
        names = sorted(
            {n.strip() for n in extra_names if n and len(n.strip()) >= 3}, key=len, reverse=True
        )
        self._own = (
            re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE)
            if names
            else None
        )

    def redact(self, text: str) -> RedactionResult:
        c: Counter[str] = Counter()
        s = text or ""

        def sub(pattern: re.Pattern, label: str, fn: Optional[Callable[[re.Match], str]] = None) -> None:
            nonlocal s

            def _r(m: re.Match) -> str:
                lab = fn(m) if fn else label
                c[lab] += 1
                return f"[{lab}]"

            s = pattern.sub(_r, s)

        # order matters: longest / most specific digit patterns first
        sub(_EMAIL, "EMAIL")
        sub(_LONG_DIGITS, "CARD", lambda m: "CARD" if luhn_ok(m.group(0)) else "ACCOUNT")
        sub(_SSN, "SSN")
        sub(_PHONE, "PHONE")
        sub(_ACCOUNT, "ACCOUNT")
        sub(_SPOKEN_DIGITS, "NUMBER")
        if self._own:
            sub(self._own, "NAME")

        def name_sub(pattern: re.Pattern) -> None:
            nonlocal s

            def _r(m: re.Match) -> str:
                words = m.group("name").split()
                lead: list[str] = []
                tail: list[str] = []
                while words and words[0].lower() in _NOT_NAMES:
                    lead.append(words.pop(0))
                while words and words[-1].lower() in _NOT_NAMES:
                    tail.insert(0, words.pop())
                if not words:  # "this is Immigration ..." is not a person
                    return m.group(0)
                c["NAME"] += 1
                prefix = m.group(0)[: m.start("name") - m.start()]
                return prefix + " ".join(lead + ["[NAME]"] + tail)

            s = pattern.sub(_r, s)

        name_sub(_NAME_CUE)
        name_sub(_TITLED_NAME)
        return RedactionResult(text=s, counts=dict(c))


_default = Redactor()


def redact(text: str) -> RedactionResult:
    return _default.redact(text)
