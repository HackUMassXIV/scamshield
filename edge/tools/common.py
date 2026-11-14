"""Shared helpers for the CLI tools."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from edge.audio.resample import resample_offline, to_mono


def load_wav(path: str | Path, target_sr: int) -> np.ndarray:
    """Any WAV/FLAC/OGG -> mono float32 at target_sr."""
    import soundfile as sf

    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    return resample_offline(to_mono(x), sr, target_sr)


def print_event(ev: dict) -> None:
    t = ev.get("type")
    if t == "transcript":
        red = f"   redacted={ev['redactions']}" if ev.get("redactions") else ""
        lat = ev.get("latency", {})
        print(
            f"  [{ev['speaker']:>7} {ev['t_start']:6.1f}s] {ev['text']}"
            f"   (asr {lat.get('asr_ms')} ms, queue {lat.get('queue_ms')} ms){red}",
            flush=True,
        )
    elif t == "risk":
        icon = {"GREEN": "[ GREEN  ]", "YELLOW": "[ YELLOW ]", "RED": "[  RED   ]"}[ev["level"]]
        f = ev.get("features", {})
        flags = ", ".join(ev.get("red_flags", [])[-4:]) or "-"
        print(f"  {icon} score {ev['score']:5.1f}  {ev.get('scam_label', ev['scam_type'])}"
              f"   (p_scam {f.get('p_scam', 0):.2f}, keywords {f.get('kw', 0):.2f}, clf {ev['latency'].get('clf_ms')} ms)"
              f"   flags: {flags}", flush=True)
    else:
        extra = {k: v for k, v in ev.items() if k not in {"type", "user_id", "ts"}}
        print(f"* {t} {json.dumps(extra)}", flush=True)


def http_to_ws(url: str) -> str:
    url = url.rstrip("/")
    if url.startswith("https://"):
        return "wss://" + url[8:]
    if url.startswith("http://"):
        return "ws://" + url[7:]
    return url
