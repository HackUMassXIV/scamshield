"""Genuine ASR errors: text -> OS TTS -> 8 kHz mu-law (phone line) -> the edge's
own VAD + Whisper -> new calls with source="whisper".

This reuses edge/audio/* and edge/asr.py, so the training text has exactly the
errors the live pipeline will produce. Run it on the demo laptop (it needs
faster-whisper weights and a TTS voice: macOS `say`, Windows System.Speech,
Linux espeak-ng).

  python -m ml.tts_asr_aug data/ml/calls_template.jsonl --n 120 --out data/ml/calls_whisper.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from edge.audio.codec import f32_to_pcm16, pcm16_to_ulaw, ulaw_to_f32
from edge.audio.resample import resample_offline, to_mono
from edge.audio.vad import EnergyVAD, Segmenter
from edge.asr import FasterWhisperASR, clean_text
from edge.config import load_settings
from ml.dataset import load_calls


def tts_to_wav(text: str, out: Path, rate: int = 175) -> None:
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    if sys.platform == "darwin":
        aiff = out.with_suffix(".aiff")
        subprocess.check_call(["say", "-r", str(rate), "-f", str(txt), "-o", str(aiff)])
        subprocess.check_call(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", str(aiff), str(out)])
    elif os.name == "nt":
        ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              f"$s.SetOutputToWaveFile('{out}');$s.Speak([IO.File]::ReadAllText('{txt}'));$s.Dispose()")
        subprocess.check_call(["powershell", "-NoProfile", "-Command", ps])
    else:
        tts = shutil.which("espeak-ng") or shutil.which("espeak")
        if not tts:
            raise SystemExit("install espeak-ng")
        subprocess.check_call([tts, "-s", "160", "-f", str(txt), "-w", str(out)])


def phone_channel(x16: np.ndarray, sr: int) -> np.ndarray:
    """Simulate the call path: resample to 8 kHz, mu-law round trip, back to 16 kHz."""
    x8 = resample_offline(x16, sr, 8000)
    x8 = ulaw_to_f32(pcm16_to_ulaw(f32_to_pcm16(x8)))
    return resample_offline(x8, 8000, 16000)


def main() -> None:
    import soundfile as sf

    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--n", type=int, default=120, help="calls to convert (balanced over labels)")
    ap.add_argument("--out", default="data/ml/calls_whisper.jsonl")
    a = ap.parse_args()

    s = load_settings()
    asr = FasterWhisperASR(s)
    calls = load_calls(a.inputs)
    random.Random(5).shuffle(calls)
    by_label: dict[str, list] = {}
    for c in calls:
        by_label.setdefault(c["label"], []).append(c)
    per = max(1, a.n // len(by_label))
    chosen = [c for lst in by_label.values() for c in lst[:per]]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td, out.open("a", encoding="utf-8") as f:
        for k, c in enumerate(chosen):
            wav = Path(td) / "call.wav"
            tts_to_wav(" ... ".join(t["text"] for t in c["turns"]), wav)
            x, sr = sf.read(str(wav), dtype="float32")
            audio = phone_channel(to_mono(x), sr)
            seg = Segmenter("mixed", EnergyVAD(), silence_ms=s.silence_ms, max_utt_s=s.max_utt_sec)
            utts = seg.push(audio) + seg.flush()
            turns = []
            for u in utts:
                text = clean_text(asr.transcribe(u.audio), s.whisper_prompt)
                if text:
                    turns.append({"speaker": "MIXED", "text": text})
            if turns:
                f.write(json.dumps({**c, "call_id": c["call_id"] + "-wh", "source": "whisper", "turns": turns}) + "\n")
            print(f"[{k + 1}/{len(chosen)}] {c['label']}: {len(turns)} utterances", flush=True)


if __name__ == "__main__":
    main()
