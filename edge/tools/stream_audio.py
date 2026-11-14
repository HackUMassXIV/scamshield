"""Mic mode and replay mode: stream local audio into the edge over /ws/audio.

Same VAD -> Whisper -> redaction pipeline as a Twilio call, no Twilio needed.
Use mic mode for the speakerphone fallback, replay mode for your recorded
(consented) demo calls.

  python -m edge.tools.stream_audio --mic --user alex
  python -m edge.tools.stream_audio --wav data/calls/scam_uscis.wav --user alex
  python -m edge.tools.stream_audio --list-devices
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import numpy as np
import websockets
from dotenv import load_dotenv

from edge.audio.codec import f32_to_pcm16
from edge.tools.common import http_to_ws, load_wav, print_event

SR = 16000
CHUNK = 320  # 20 ms


async def listen_ui(url: str, user: str, key: str, stop: asyncio.Event) -> None:
    async with websockets.connect(f"{http_to_ws(url)}/ws/ui?user_id={user}&key={key}") as ws:
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("replay"):
                continue  # history from earlier calls
            print_event(ev)
            if ev.get("type") == "call_ended":
                stop.set()
                return


async def send_wav(ws, path: str, fast: bool) -> None:
    x = load_wav(path, SR)
    pcm = f32_to_pcm16(x)
    t0 = time.perf_counter()
    for n, i in enumerate(range(0, len(pcm), CHUNK), start=1):
        await ws.send(pcm[i : i + CHUNK].tobytes())
        if not fast:
            lag = n * CHUNK / SR - (time.perf_counter() - t0)
            if lag > 0:
                await asyncio.sleep(lag)
    await ws.send(np.zeros(SR, np.int16).tobytes())  # 1 s of silence closes the last utterance


async def send_mic(ws, device, stop: asyncio.Event) -> None:
    import sounddevice as sd

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)

    def cb(indata, frames, t, status):  # PortAudio thread
        if status:
            print(f"! mic: {status}", file=sys.stderr)
        loop.call_soon_threadsafe(lambda d=indata[:, 0].copy(): q.full() or q.put_nowait(d))

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK, device=device, callback=cb):
        print("* mic open, speak (Ctrl+C to stop)")
        while not stop.is_set():
            data = await q.get()
            await ws.send(data.tobytes())


async def run(a: argparse.Namespace) -> int:
    load_dotenv()
    key = os.getenv("GUARDIAN_API_KEY", "")
    stop = asyncio.Event()
    ui = asyncio.create_task(listen_ui(a.url, a.user, key, stop))
    await asyncio.sleep(0.3)
    uri = f"{http_to_ws(a.url)}/ws/audio?user_id={a.user}&key={key}&speaker={a.speaker}"
    async with websockets.connect(uri, max_size=None) as ws:
        hello = json.loads(await ws.recv())
        print(f"* local session {hello.get('call_id')}")
        try:
            if a.wav:
                await send_wav(ws, a.wav, a.fast)
                await asyncio.sleep(0.5)
            else:
                await send_mic(ws, a.device, stop)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        await ws.send("stop")
        try:
            async for raw in ws:
                if isinstance(raw, str) and json.loads(raw).get("ended"):
                    break
        except websockets.ConnectionClosed:
            pass
    try:
        await asyncio.wait_for(stop.wait(), timeout=a.wait)
    except asyncio.TimeoutError:
        pass
    ui.cancel()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--mic", action="store_true")
    src.add_argument("--wav")
    src.add_argument("--list-devices", action="store_true")
    ap.add_argument("--device", default=None, help="sounddevice input device index or name")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--user", default="alex")
    ap.add_argument("--speaker", default="mixed", choices=["mixed", "caller", "student"])
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--wait", type=float, default=60.0)
    a = ap.parse_args()
    if a.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    if not a.mic and not a.wav:
        ap.error("pass --mic or --wav FILE")
    if a.device is not None and str(a.device).isdigit():
        a.device = int(a.device)
    try:
        raise SystemExit(asyncio.run(run(a)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
