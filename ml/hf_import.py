"""Import a public scam-dialogue dataset from Hugging Face into our call format.

Dataset names and columns differ, so nothing is hard-coded: search the Hub for
"scam dialogue", "phone scam", "fraud call" and map its columns here. Check each
dataset's license before using it.

  python -m ml.hf_import OWNER/DATASET --text-col dialogue --label-col label \
      --scam-values 1,scam --split train --out data/ml/calls_hf.jsonl

Every scam row gets the label you pass with --scam-label (default: let the
classifier's generic scam classes be learned from templates/Gemini, and keep HF
data as an extra *test* set with --test-only, which is the recommended use).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_SPEAKER = re.compile(r"^\s*(caller|suspect|scammer|innocent|victim|receiver|agent|customer|speaker\s*\d|[ab])\s*[:\-]\s*", re.I)


def to_turns(text: str) -> list[dict]:
    parts = [p for p in re.split(r"\n+|(?<=[.?!])\s+(?=[A-Za-z ]{1,12}:)", text) if p.strip()]
    turns = []
    for p in parts:
        m = _SPEAKER.match(p)
        spk = "CALLER" if (m and m.group(1).lower() in {"caller", "suspect", "scammer", "agent", "a"}) else "STUDENT" if m else "CALLER"
        turns.append({"speaker": spk, "text": _SPEAKER.sub("", p).strip()})
    return [t for t in turns if t["text"]]


def main() -> None:
    from datasets import load_dataset  # pip install datasets

    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("--split", default="train")
    ap.add_argument("--text-col", required=True)
    ap.add_argument("--label-col", required=True)
    ap.add_argument("--scam-values", default="1,scam,fraud,true")
    ap.add_argument("--scam-label", default="bank_impersonation", help="our label for scam rows")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--test-only", action="store_true", help="mark as external test data")
    ap.add_argument("--out", default="data/ml/calls_hf.jsonl")
    a = ap.parse_args()
    scam_vals = {v.strip().lower() for v in a.scam_values.split(",")}
    ds = load_dataset(a.dataset, split=a.split)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for i, row in enumerate(ds):
            if n >= a.limit:
                break
            turns = to_turns(str(row[a.text_col]))
            if len(turns) < 2:
                continue
            is_scam = str(row[a.label_col]).strip().lower() in scam_vals
            f.write(json.dumps({
                "call_id": f"hf-{i:05d}", "label": a.scam_label if is_scam else "benign",
                "subtype": "scam" if is_scam else "normal:hf", "intl": False,
                "source": "hf_test" if a.test_only else "hf", "turns": turns}) + "\n")
            n += 1
    print(f"wrote {n} calls to {out}")


if __name__ == "__main__":
    main()
