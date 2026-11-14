"""Text normalization applied IDENTICALLY at training time (ml/) and at
inference time (edge/). Whisper output is cased and punctuated while synthetic
data may not be; normalizing both the same way removes that mismatch.

Input is expected to be REDACTED already, so [NAME]/[PHONE]/... tags survive
as single lowercase tokens (`[phone]`).
"""
from __future__ import annotations

import re

_TAG = re.compile(r"\[(NAME|PHONE|SSN|CARD|ACCOUNT|EMAIL|NUMBER)\]", re.IGNORECASE)
_KEEP = re.compile(r"[^a-z0-9$\[\]' ]+")
_SPACES = re.compile(r"\s+")
_MONEY = re.compile(r"\$\s?(\d[\d,]*)")


def normalize_for_clf(text: str) -> str:
    s = (text or "").lower()
    s = _TAG.sub(lambda m: f" [{m.group(1).lower()}] ", s)
    s = _MONEY.sub(lambda m: f"{m.group(1).replace(',', '')} dollars", s)  # "$1,200" -> "1200 dollars"
    s = re.sub(r"(?<=\d),(?=\d)", "", s)  # "1,200 dollars" -> "1200 dollars"
    s = s.replace("-", " ")
    s = _KEEP.sub(" ", s)
    s = re.sub(r"(?<![a-z])'|'(?![a-z])", " ", s)  # stray quotes
    return _SPACES.sub(" ", s).strip()
