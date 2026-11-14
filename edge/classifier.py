"""Scam classifier on the edge, with automatic fallback:

  1. models/clf/            fine-tuned DistilBERT (ml/train_distilbert.py) via transformers
  2. models/clf_tfidf.joblib  TF-IDF + logistic regression (ml/train_baseline.py)
  3. keywords               always available

CLF_BACKEND=auto|hf|tfidf|keyword picks one explicitly. Input is the REDACTED
window; it is normalized here exactly as in training (shared/textnorm.py).
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from shared.labels import LABELS
from shared.textnorm import normalize_for_clf

from .config import ROOT, Settings
from .keywords import keyword_label_probs

log = logging.getLogger("guardian.clf")


class Classifier(Protocol):
    name: str

    def predict(self, norm_text: str) -> dict[str, float]: ...


class KeywordClassifier:
    name = "keyword"

    def predict(self, norm_text: str) -> dict[str, float]:
        return keyword_label_probs(norm_text)


class TfidfClassifier:
    name = "tfidf"

    def __init__(self, path: Path):
        import joblib

        blob = joblib.load(path)
        self.pipe = blob["pipeline"]
        self._order = [list(self.pipe.classes_).index(l) for l in LABELS]

    def predict(self, norm_text: str) -> dict[str, float]:
        p = self.pipe.predict_proba([norm_text])[0]
        return {l: float(p[i]) for l, i in zip(LABELS, self._order)}


class HFClassifier:
    name = "distilbert"

    def __init__(self, model_dir: Path):
        from transformers import pipeline  # lazy: heavy import

        self.pipe = pipeline("text-classification", model=str(model_dir), top_k=None, device=-1)

    def predict(self, norm_text: str) -> dict[str, float]:
        out = self.pipe(norm_text, truncation=True, max_length=128)
        if out and isinstance(out[0], list):  # some versions nest the list
            out = out[0]
        probs = {l: 0.0 for l in LABELS}
        for d in out:
            lab = d["label"]
            if lab.startswith("LABEL_"):  # config without id2label
                lab = LABELS[int(lab.split("_")[1])]
            if lab in probs:
                probs[lab] = float(d["score"])
        return probs


def load_classifier(s: Settings) -> Classifier:
    want = s.clf_backend
    hf_dir = Path(s.clf_dir) if Path(s.clf_dir).is_absolute() else ROOT / s.clf_dir
    tfidf = Path(s.clf_tfidf) if Path(s.clf_tfidf).is_absolute() else ROOT / s.clf_tfidf
    tries = {"hf": [("hf", hf_dir)], "tfidf": [("tfidf", tfidf)], "keyword": [],
             "auto": [("hf", hf_dir), ("tfidf", tfidf)]}.get(want, [("hf", hf_dir), ("tfidf", tfidf)])
    for kind, path in tries:
        try:
            if kind == "hf" and (path / "config.json").exists():
                return HFClassifier(path)
            if kind == "tfidf" and path.exists():
                return TfidfClassifier(path)
        except Exception as exc:
            log.warning("could not load %s classifier from %s: %s", kind, path, exc)
    if want not in ("auto", "keyword"):
        log.warning("CLF_BACKEND=%s not available; using keyword classifier", want)
    else:
        log.info("no trained model found; using keyword classifier (run: python run.py train)")
    return KeywordClassifier()


class ClassifierService:
    """Own worker thread, separate from Whisper's, so classification never waits on ASR."""

    def __init__(self, clf: Classifier):
        self.clf = clf
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clf")

    @property
    def name(self) -> str:
        return self.clf.name

    async def predict(self, redacted_text: str) -> tuple[dict[str, float], float, str]:
        norm = normalize_for_clf(redacted_text)
        t0 = time.perf_counter()
        probs = await asyncio.get_running_loop().run_in_executor(self._pool, self.clf.predict, norm)
        return probs, (time.perf_counter() - t0) * 1000, norm

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
