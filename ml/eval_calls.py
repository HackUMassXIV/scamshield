"""Call-level evaluation: replays each held-out call utterance by utterance
through the SAME risk engine the edge runs (edge/risk.CallRisk.step) and
reports what a judge cares about:

  - scam calls: % reaching RED / YELLOW, utterances until RED
  - benign calls: % false RED / false YELLOW, normal vs urgent-but-legit
  - external set: the hand-written scripts in data/calls/scripts (never trained on)
  - ablation: keywords only / classifier only / classifier + keywords

  python -m ml.eval_calls                      # uses the best model the edge would load
  -> reports/calls_<model>.md
"""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from pathlib import Path

from edge.classifier import KeywordClassifier, load_classifier
from edge.config import load_settings
from edge.risk import LEVELS, CallRisk
from ml.dataset import load_calls, prep_turn, split_by_call
from shared.labels import LABELS
from shared.textnorm import normalize_for_clf

SECONDS_PER_UTTERANCE = 4.0
SCRIPT_LABELS = {"scam_uscis": "immigration_uscis", "scam_irs": "irs_gov",
                 "scam_rental_sublet": "rental_deposit", "scam_job_check": "fake_job_check",
                 "scam_grandparent": "family_emergency", "scam_tech_refund": "tech_support",
                 "scam_bank_otp": "bank_impersonation"}  # benign_* files are benign


def external_calls(folder: Path) -> list[dict]:
    calls = []
    for f in sorted(folder.glob("*.txt")):
        label = SCRIPT_LABELS.get(f.stem) or ("benign" if f.stem.startswith("benign") else None)
        if label is None:
            continue
        turns = [{"speaker": "CALLER", "text": l.strip()} for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        sub = "urgent:script" if label == "benign" else "scam"
        calls.append({"call_id": f"script-{f.stem}", "label": label, "subtype": sub, "source": "script", "turns": turns})
    return calls


def run_call(call: dict, clf, drop=frozenset()) -> dict:
    cr = CallRisk(drop=drop)
    utts = [prep_turn(t["text"]) for t in call["turns"]]
    windows: list[str] = []
    first = {"YELLOW": None, "RED": None}
    for i, u in enumerate(utts):
        if not u:
            continue
        windows.append(u)
        norm = normalize_for_clf(" ".join(windows[-3:]))
        r = cr.step(clf.predict(norm), norm, now=i * SECONDS_PER_UTTERANCE)
        for lv in ("YELLOW", "RED"):
            if first[lv] is None and LEVELS.index(r["level"]) >= LEVELS.index(lv):
                first[lv] = i + 1
    return {"max": cr.max_level, "first": first, "type": r["scam_type"] if utts else "benign"}


def summarize(calls: list[dict], clf, drop=frozenset()) -> dict:
    res = defaultdict(list)
    for c in calls:
        out = run_call(c, clf, drop)
        key = c["label"] if c["label"] != "benign" else ("benign_urgent" if c.get("subtype", "").startswith("urgent") else "benign_normal")
        res[key].append((c, out))
    table = {}
    for key, items in res.items():
        n = len(items)
        red = sum(o["max"] == "RED" for _, o in items)
        yel = sum(o["max"] in ("YELLOW", "RED") for _, o in items)
        to_red = [o["first"]["RED"] for _, o in items if o["first"]["RED"]]
        typed = sum(o["type"] == c["label"] for c, o in items) if not key.startswith("benign") else None
        table[key] = {"n": n, "red": red / n, "yellow_plus": yel / n,
                      "median_utts_to_red": statistics.median(to_red) if to_red else None,
                      "type_correct": (typed / n) if typed is not None else None}
    return table


def fmt_table(title: str, t: dict) -> list[str]:
    f = lambda x: "-" if x is None else (f"{x:.0%}" if isinstance(x, float) and x <= 1 else f"{x}")
    lines = [f"### {title}", "", "| class | calls | reached RED | YELLOW or RED | median utterances to RED | scam type correct |",
             "|---|---|---|---|---|---|"]
    order = [l for l in LABELS if l != "benign"] + ["benign_normal", "benign_urgent"]
    for k in order:
        if k in t:
            m = t[k]
            lines.append(f"| {k} | {m['n']} | {f(m['red'])} | {f(m['yellow_plus'])} | {f(m['median_utts_to_red'])} | {f(m['type_correct'])} |")
    return lines + [""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", default=["data/ml/calls_*.jsonl"])
    ap.add_argument("--scripts", default="data/calls/scripts")
    a = ap.parse_args()
    clf = load_classifier(load_settings())
    calls = load_calls(a.inputs)
    _, test = split_by_call([c for c in calls if c.get("source") != "hf_test"])  # same split as training
    test += [c for c in calls if c.get("source") == "hf_test"]
    ext = external_calls(Path(a.scripts))

    lines = [f"# Call-level evaluation: {clf.name}", "",
             f"Each call is replayed utterance by utterance ({SECONDS_PER_UTTERANCE:.0f} s apart) through edge/risk.py.", ""]
    lines += fmt_table(f"Held-out calls ({len(test)})", summarize(test, clf))
    if ext:
        lines += fmt_table(f"External hand-written scripts ({len(ext)}, never trained on)", summarize(ext, clf))
        for c in ext:
            o = run_call(c, clf)
            lines.append(f"- `{c['call_id']}` ({c['label']}): max **{o['max']}**, RED at utterance {o['first']['RED']}, type {o['type']}")
        lines.append("")
    lines += ["## Ablation (held-out calls)", ""]
    for name, model, drop in (("keywords only", KeywordClassifier(), frozenset({"clf"})),
                              ("classifier only", clf, frozenset({"kw"})),
                              ("classifier + keywords", clf, frozenset())):
        t = summarize(test, model, drop)
        scams = [v for k, v in t.items() if not k.startswith("benign")]
        n = sum(v["n"] for v in scams)
        red = sum(v["red"] * v["n"] for v in scams) / n
        fr = {k: t[k]["red"] for k in ("benign_normal", "benign_urgent") if k in t}
        fy = {k: t[k]["yellow_plus"] for k in ("benign_normal", "benign_urgent") if k in t}
        lines.append(f"- **{name}**: scams reaching RED {red:.0%} | false RED normal {fr.get('benign_normal', 0):.0%}, "
                     f"urgent {fr.get('benign_urgent', 0):.0%} | false YELLOW+ normal {fy.get('benign_normal', 0):.0%}, "
                     f"urgent {fy.get('benign_urgent', 0):.0%}")
    out = Path("reports") / f"calls_{clf.name}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
