"""Metrics shared by both trainers so TF-IDF and DistilBERT are reported identically."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.metrics import classification_report, confusion_matrix, f1_score

from shared.labels import LABELS


def ece(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error of the top-label confidence."""
    conf = probs.max(1)
    pred = probs.argmax(1)
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            total += m.mean() * abs((pred[m] == y[m]).mean() - conf[m].mean())
    return float(total)


def p_scam(probs: np.ndarray) -> np.ndarray:
    return 1.0 - probs[:, LABELS.index("benign")]


def evaluate(probs: np.ndarray, rows: list[dict], threshold: float = 0.5) -> dict:
    y = np.array([LABELS.index(r["label"]) for r in rows])
    pred = probs.argmax(1)
    ps = p_scam(probs)
    is_scam = y != LABELS.index("benign")
    urgent = np.array([r.get("subtype", "").startswith("urgent") for r in rows])
    normal = (~is_scam) & (~urgent)
    out = {
        "n": int(len(rows)),
        "accuracy": float((pred == y).mean()),
        "macro_f1": float(f1_score(y, pred, average="macro", labels=range(len(LABELS)), zero_division=0)),
        "ece": ece(probs, y),
        # binary view: is it a scam at all? (what drives the color)
        "scam_recall@thr": float((ps[is_scam] >= threshold).mean()) if is_scam.any() else None,
        "benign_fpr@thr": float((ps[~is_scam] >= threshold).mean()) if (~is_scam).any() else None,
        "benign_urgent_fpr@thr": float((ps[urgent] >= threshold).mean()) if urgent.any() else None,
        "benign_normal_fpr@thr": float((ps[normal] >= threshold).mean()) if normal.any() else None,
        "threshold": threshold,
        "per_label": {k: v for k, v in classification_report(
            y, pred, labels=range(len(LABELS)), target_names=LABELS, output_dict=True, zero_division=0).items()
            if k in LABELS},
        "confusion": confusion_matrix(y, pred, labels=range(len(LABELS))).tolist(),
    }
    return out


def by_variant(probs: np.ndarray, rows: list[dict]) -> dict:
    """The ablation: clean vs simulated-ASR noise vs real Whisper vs external data."""
    res = {"all": evaluate(probs, rows)}
    for v in sorted({r["variant"] for r in rows} | {"hf_test"}):
        idx = [i for i, r in enumerate(rows) if (r["variant"] == v if v != "hf_test" else r.get("source") == "hf_test")]
        if idx:
            res[v] = evaluate(probs[idx], [rows[i] for i in idx])
    return res


def write_report(res: dict, name: str, out_dir: str | Path = "reports", extra: dict | None = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"metrics_{name}.json").write_text(json.dumps({**(extra or {}), **res}, indent=2))
    lines = [f"# Classifier report: {name}", ""]
    if extra:
        lines += [f"- {k}: {v}" for k, v in extra.items()] + [""]
    lines += ["| variant | n | acc | macro F1 | ECE | scam recall | benign FPR | benign-urgent FPR |",
              "|---|---|---|---|---|---|---|---|"]
    f = lambda x: "-" if x is None else f"{x:.3f}"
    for v, m in res.items():
        lines.append(f"| {v} | {m['n']} | {f(m['accuracy'])} | {f(m['macro_f1'])} | {f(m['ece'])} | "
                     f"{f(m['scam_recall@thr'])} | {f(m['benign_fpr@thr'])} | {f(m['benign_urgent_fpr@thr'])} |")
    m = res["all"]
    lines += ["", "## Per-label F1 (all test windows)", "", "| label | precision | recall | F1 | support |", "|---|---|---|---|---|"]
    for l in LABELS:
        d = m["per_label"][l]
        lines.append(f"| {l} | {d['precision']:.3f} | {d['recall']:.3f} | {d['f1-score']:.3f} | {int(d['support'])} |")
    lines += ["", "## Confusion matrix (rows = true, cols = predicted)", "",
              "| | " + " | ".join(l[:10] for l in LABELS) + " |", "|---" * (len(LABELS) + 1) + "|"]
    for l, row in zip(LABELS, m["confusion"]):
        lines.append(f"| **{l[:10]}** | " + " | ".join(str(x) for x in row) + " |")
    p = out / f"metrics_{name}.md"
    p.write_text("\n".join(lines) + "\n")
    return p
