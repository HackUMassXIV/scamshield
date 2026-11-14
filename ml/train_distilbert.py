"""Fine-tune DistilBERT on the windows (run on Colab with a T4, ~10 min).

Colab:
  1. Runtime -> Change runtime type -> T4 GPU
  2. Upload a zip of the repo (or git clone it), then:
       !pip -q install transformers datasets accelerate scikit-learn
       %cd scamshield
       !python -m ml.train_distilbert
  3. Download models/clf (zip it: !zip -r clf.zip models/clf reports) and unzip
     into the laptop's scamshield/models/clf. The edge picks it up automatically.

Same windows, same metrics and report format as the TF-IDF baseline.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from ml.metrics import by_variant, write_report
from shared.labels import ID2LABEL, LABEL2ID, LABELS


def load(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    import torch
    from datasets import Dataset
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding,
                              Trainer, TrainingArguments)

    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/ml/windows_train.jsonl")
    ap.add_argument("--test", default="data/ml/windows_test.jsonl")
    ap.add_argument("--base", default="distilbert-base-uncased")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--out", default="models/clf")
    a = ap.parse_args()

    tr, te = load(a.train), load(a.test)
    tok = AutoTokenizer.from_pretrained(a.base)

    def ds(rows):
        d = Dataset.from_dict({"text": [r["text"] for r in rows], "label": [LABEL2ID[r["label"]] for r in rows]})
        return d.map(lambda b: tok(b["text"], truncation=True, max_length=a.max_len), batched=True)

    dtr, dte = ds(tr), ds(te)
    model = AutoModelForSequenceClassification.from_pretrained(
        a.base, num_labels=len(LABELS), id2label=ID2LABEL, label2id=LABEL2ID)
    args = TrainingArguments(
        output_dir="runs/clf", num_train_epochs=a.epochs, learning_rate=a.lr,
        per_device_train_batch_size=a.bs, per_device_eval_batch_size=128,
        eval_strategy="epoch", save_strategy="no", logging_steps=100,
        fp16=torch.cuda.is_available(), report_to=[], seed=13)
    # same weighting as the baseline: benign vs all scams 50/50 (see ml/train_baseline.sample_weights)
    from collections import Counter

    n = Counter(r["label"] for r in tr)
    share = {l: 0.5 if l == "benign" else 0.5 / (len(LABELS) - 1) for l in LABELS}
    cw = torch.tensor([share[l] / max(1, n[l]) for l in LABELS], dtype=torch.float)
    cw = cw / cw.mean()

    class WeightedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kw):
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            loss = torch.nn.functional.cross_entropy(outputs.logits, labels, weight=cw.to(outputs.logits.device))
            return (loss, outputs) if return_outputs else loss

    trainer = WeightedTrainer(model=model, args=args, train_dataset=dtr, eval_dataset=dte,
                              data_collator=DataCollatorWithPadding(tok), processing_class=tok)
    t0 = time.time()
    trainer.train()
    train_s = time.time() - t0

    logits = trainer.predict(dte).predictions
    probs = torch.softmax(torch.tensor(logits), dim=-1).numpy()
    trainer.save_model(a.out)
    tok.save_pretrained(a.out)

    # CPU latency, as the laptop will see it
    model_cpu = model.to("cpu").eval()
    sample = [r["text"] for r in te[:200]]
    with torch.no_grad():
        t0 = time.perf_counter()
        for s in sample:
            model_cpu(**tok(s, return_tensors="pt", truncation=True, max_length=a.max_len))
        cpu_ms = (time.perf_counter() - t0) * 1000 / len(sample)

    res = by_variant(np.asarray(probs), te)
    write_report(res, "distilbert", extra={
        "model": a.base, "epochs": a.epochs, "lr": a.lr, "batch": a.bs,
        "train_windows": len(tr), "test_windows": len(te),
        "train_seconds": round(train_s), "cpu_ms_per_window": round(cpu_ms, 1)})
    m = res["all"]
    print(f"saved {a.out} | macro F1 {m['macro_f1']:.3f} | ECE {m['ece']:.3f} | "
          f"benign-urgent FPR {m['benign_urgent_fpr@thr']:.3f} | CPU {cpu_ms:.1f} ms/window")


if __name__ == "__main__":
    main()
