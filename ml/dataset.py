"""Calls -> classifier windows, split BY CALL (never by window).

Pipeline per turn, exactly as on the edge: redact -> normalize_for_clf.
Windows are the last 1-3 turns ending at each turn, because the edge classifies
`session.recent_text(3)` after every utterance, including the first one.

Variants (for the ablation):
  clean     redacted + normalized text
  noisy     clean + ml.asr_noise (simulated Whisper errors)
  whisper   calls whose turns came from real Whisper (ml/tts_asr_aug.py)

  python -m ml.dataset data/ml/calls_*.jsonl --out data/ml
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import zlib
from collections import Counter, defaultdict
from pathlib import Path

from edge.redact import Redactor
from ml.asr_noise import asr_noise
from shared.labels import LABELS
from shared.textnorm import normalize_for_clf

_red = Redactor()


def load_calls(patterns: list[str]) -> list[dict]:
    calls, seen = [], set()
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                c = json.loads(line)
                if c.get("label") not in LABELS or not c.get("turns") or c["call_id"] in seen:
                    continue
                seen.add(c["call_id"])
                calls.append(c)
    return calls


def prep_turn(text: str) -> str:
    return normalize_for_clf(_red.redact(text).text)


def windows_for_call(call: dict, sizes=(1, 2, 3), noisy: bool = False, seed: int = 0) -> list[dict]:
    turns = [prep_turn(t["text"]) for t in call["turns"]]
    turns = [t for t in turns if t]
    variant = "whisper" if call.get("source") == "whisper" else "clean"
    if noisy:
        turns = [asr_noise(t, seed=zlib.crc32(f"{call['call_id']}|{i}|{seed}".encode())) for i, t in enumerate(turns)]
        variant = "noisy"
    out = []
    for i in range(len(turns)):
        for w in sizes:
            if i + 1 >= w:
                out.append({
                    "text": " ".join(turns[i - w + 1 : i + 1]),
                    "label": call["label"],
                    "call_id": call["call_id"],
                    "subtype": call.get("subtype", ""),
                    "source": call.get("source", ""),
                    "variant": variant,
                    "pos": i,
                    "size": w,
                })
    return out


def split_by_call(calls: list[dict], test_frac: float = 0.15, seed: int = 13) -> tuple[list[dict], list[dict]]:
    """Stratified by label; every window of a call lands on the same side."""
    r = random.Random(seed)
    by_label: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        by_label[(c["label"], c.get("source", ""))].append(c)
    train, test = [], []
    for group in by_label.values():
        r.shuffle(group)
        k = max(1, round(len(group) * test_frac)) if len(group) > 1 else 0
        test += group[:k]
        train += group[k:]
    return train, test


def build(calls: list[dict], test_frac: float = 0.15, seed: int = 13, augment: int = 1):
    external = [c for c in calls if c.get("source") == "hf_test"]  # test-only data
    tr_calls, te_calls = split_by_call([c for c in calls if c.get("source") != "hf_test"], test_frac, seed)
    te_calls += external
    train, test = [], []
    for c in tr_calls:
        train += windows_for_call(c)
        for k in range(augment):  # noisy copies for robustness
            train += windows_for_call(c, noisy=True, seed=k)
    for c in te_calls:
        test += windows_for_call(c)
        if c.get("source") != "whisper":
            test += windows_for_call(c, noisy=True, seed=99)
    assert not ({w["call_id"] for w in train} & {w["call_id"] for w in test}), "call leakage"
    return train, test, tr_calls, te_calls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default="data/ml")
    ap.add_argument("--test-frac", type=float, default=0.15)
    a = ap.parse_args()
    calls = load_calls(a.inputs)
    train, test, trc, tec = build(calls, a.test_frac)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("windows_train", train), ("windows_test", test)):
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    print(f"{len(calls)} calls -> train {len(trc)} calls / {len(train)} windows, test {len(tec)} calls / {len(test)} windows")
    print("labels:", dict(Counter(c["label"] for c in calls)))


if __name__ == "__main__":
    main()
