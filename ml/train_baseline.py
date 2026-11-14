"""TF-IDF + logistic regression: the baseline AND the edge's fallback model.

Trains in seconds on a laptop, runs in ~1 ms per window, needs no torch.

  python -m ml.train_baseline                 # uses data/ml/windows_{train,test}.jsonl
  -> models/clf_tfidf.joblib, reports/metrics_tfidf.{md,json}
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from ml.metrics import by_variant, write_report
from shared.labels import LABELS


def load(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def make_pipeline(C: float = 4.0) -> Pipeline:
    feats = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, token_pattern=r"[\w$\[\]']+")),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, sublinear_tf=True, max_features=60000)),
    ])
    clf = LogisticRegression(C=C, max_iter=3000)
    return Pipeline([("feats", feats), ("lr", clf)])


def sample_weights(labels: list[str]) -> np.ndarray:
    """Benign vs scam weighted 50/50, scam labels equal among themselves.
    (sklearn's class_weight="balanced" would give benign a 1/8 prior, so text
    with no information, e.g. "okay thanks", would default to ~7/8 scam.)"""
    from collections import Counter

    n = Counter(labels)
    n_scam_labels = len([l for l in LABELS if l != "benign" and n[l]])
    w = {l: (0.5 / n[l]) if l == "benign" else (0.5 / n_scam_labels / n[l]) for l in n}
    arr = np.array([w[l] for l in labels])
    return arr * len(labels) / arr.sum()


def proba_in_label_order(pipe: Pipeline, texts: list[str]) -> np.ndarray:
    raw = pipe.predict_proba(texts)
    order = [list(pipe.classes_).index(l) for l in LABELS]
    return raw[:, order]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/ml/windows_train.jsonl")
    ap.add_argument("--test", default="data/ml/windows_test.jsonl")
    ap.add_argument("--C", type=float, default=4.0)
    ap.add_argument("--out", default="models/clf_tfidf.joblib")
    a = ap.parse_args()

    tr, te = load(a.train), load(a.test)
    t0 = time.time()
    y = [r["label"] for r in tr]
    pipe = make_pipeline(a.C).fit([r["text"] for r in tr], y, lr__sample_weight=sample_weights(y))
    train_s = time.time() - t0

    texts = [r["text"] for r in te]
    t0 = time.perf_counter()
    probs = proba_in_label_order(pipe, texts)
    ms = (time.perf_counter() - t0) * 1000 / max(1, len(texts))

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipe, "labels": LABELS, "textnorm": "v1", "trained_at": time.time()}, a.out)
    res = by_variant(probs, te)
    report = write_report(res, "tfidf", extra={
        "model": "tfidf(word1-2 + char3-5) + logistic regression",
        "train_windows": len(tr), "test_windows": len(te),
        "train_seconds": round(train_s, 1), "ms_per_window": round(ms, 3)})
    m = res["all"]
    print(f"saved {a.out}  ({Path(a.out).stat().st_size / 1e6:.1f} MB)")
    print(f"macro F1 {m['macro_f1']:.3f} | ECE {m['ece']:.3f} | scam recall {m['scam_recall@thr']:.3f} | "
          f"benign-urgent FPR {m['benign_urgent_fpr@thr']:.3f} | {ms:.2f} ms/window")
    print(f"report: {report}")


if __name__ == "__main__":
    main()
