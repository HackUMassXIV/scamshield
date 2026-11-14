"""Simulate the WHOLE Twilio merge flow against a running edge, no phones needed.

It does exactly what Twilio + the PWA would do:
  1. POST /twilio/voice  (form-encoded, correctly signed with your auth token,
     From = the student's phone)                  -> session, leg_connected
  2. POST /api/screen/start  (the PWA button)     -> disclosure TwiML (dry-run logs it)
  3. WS /ws/twilio: Twilio's "connected" / "start" (with call_id) / "media" /
     "stop" messages, streaming your WAV as 8 kHz mu-law at real-time speed
  4. listens on /ws/ui and prints what the PWA would show

Run the edge with TWILIO_DRY_RUN=1 (so step 2 doesn't call the real Twilio API):

  TWILIO_DRY_RUN=1 uvicorn edge.main:app --port 8000
  python -m edge.tools.simulate_twilio data/calls/scam_uscis.wav --user alex

Use the same PUBLIC_BASE_URL and TWILIO_AUTH_TOKEN as the server (read from .env),
so the signature check is exercised for real.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import sys
import time

import httpx
import numpy as np
import websockets
from dotenv import load_dotenv

from edge.audio.codec import f32_to_pcm16, pcm16_to_ulaw
from edge.tools.common import http_to_ws, load_wav, print_event

FRAME = 160  # 20 ms of 8 kHz audio, what Twilio sends per media message


async def listen_ui(url: str, user: str, key: str, done: asyncio.Event) -> None:
    async with websockets.connect(f"{http_to_ws(url)}/ws/ui?user_id={user}&key={key}") as ws:
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("replay"):
                continue  # history from earlier runs
            print_event(ev)
            if ev.get("type") == "call_ended":
                done.set()
                return


async def run(a: argparse.Namespace) -> int:
    load_dotenv()
    from twilio.request_validator import RequestValidator

    base = a.url.rstrip("/")
    public = (os.getenv("PUBLIC_BASE_URL") or base).rstrip("/")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    key = os.getenv("GUARDIAN_API_KEY", "")
    phone = a.phone

    if not phone:  # the edge runs on this same laptop: read its users.json
        from edge.config import load_settings
        from edge.users import UserDirectory

        u = UserDirectory(load_settings().users_file).by_id(a.user)
        phone = u.phone if u else ""
    if not phone:
        print(f"user {a.user!r} not found in users.json; pass --phone", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(timeout=30) as http:
        done = asyncio.Event()
        ui = asyncio.create_task(listen_ui(base, a.user, key, done))
        await asyncio.sleep(0.3)

        # 1) the Guardian leg rings in (signed like Twilio does it)
        call_sid = "CA" + secrets.token_hex(16)
        params = {
            "CallSid": call_sid,
            "AccountSid": os.getenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32),
            "From": phone,
            "To": os.getenv("TWILIO_NUMBER", "+10000000000"),
            "CallStatus": "ringing",
            "Direction": "inbound",
        }
        sig = RequestValidator(token).compute_signature(f"{public}/twilio/voice", params)
        r = await http.post(f"{base}/twilio/voice", data=params, headers={"X-Twilio-Signature": sig})
        print(f"> /twilio/voice -> {r.status_code}\n{r.text}")
        if r.status_code != 200 or "<Pause" not in r.text:
            ui.cancel()
            return 1

        # 2) the student taps "I've merged, start screening"
        await asyncio.sleep(a.merge_delay)
        r = await http.post(
            f"{base}/api/screen/start", json={"user_id": a.user}, headers={"X-Guardian-Key": key}
        )
        print(f"> /api/screen/start -> {r.status_code} {r.text}")
        if r.status_code != 200:
            ui.cancel()
            return 1
        call_id = r.json()["call_id"]

    # 3) Twilio opens the media stream (after the ~4 s disclosure in real life)
    x8 = load_wav(a.wav, 8000)
    if a.gain != 1.0:
        x8 = np.clip(x8 * a.gain, -1, 1)
    pcm = f32_to_pcm16(x8)
    stream_sid = "MZ" + secrets.token_hex(16)
    async with websockets.connect(f"{http_to_ws(base)}/ws/twilio") as ws:
        await ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        await ws.send(json.dumps({
            "event": "start",
            "sequenceNumber": "1",
            "streamSid": stream_sid,
            "start": {
                "streamSid": stream_sid,
                "accountSid": params["AccountSid"],
                "callSid": call_sid,
                "tracks": ["inbound"],
                "customParameters": {"call_id": call_id},
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        }))
        t0 = time.perf_counter()
        n = 0
        for i in range(0, len(pcm), FRAME):
            chunk = pcm[i : i + FRAME]
            if chunk.size < FRAME:
                chunk = np.pad(chunk, (0, FRAME - chunk.size))
            n += 1
            await ws.send(json.dumps({
                "event": "media",
                "sequenceNumber": str(n + 1),
                "streamSid": stream_sid,
                "media": {
                    "track": "inbound",
                    "chunk": str(n),
                    "timestamp": str(n * 20),
                    "payload": base64.b64encode(pcm16_to_ulaw(chunk)).decode(),
                },
            }))
            if not a.fast:  # pace at real time, like a live call
                lag = n * 0.02 - (time.perf_counter() - t0)
                if lag > 0:
                    await asyncio.sleep(lag)
        await asyncio.sleep(1.0)  # let the last utterance close
        await ws.send(json.dumps({"event": "stop", "streamSid": stream_sid, "stop": {"callSid": call_sid}}))
    print(f"> streamed {len(pcm) / 8000:.1f}s of audio in {time.perf_counter() - t0:.1f}s")

    try:
        await asyncio.wait_for(done.wait(), timeout=a.wait)
    except asyncio.TimeoutError:
        print("! no call_ended event received", file=sys.stderr)
        return 1
    finally:
        ui.cancel()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav", help="audio file (any sample rate; your own consented recording or TTS)")
    ap.add_argument("--url", default="http://localhost:8000", help="edge base URL")
    ap.add_argument("--user", default="alex")
    ap.add_argument("--phone", default="", help="override the student's phone (caller ID)")
    ap.add_argument("--merge-delay", type=float, default=0.5)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--fast", action="store_true", help="send as fast as possible (stress test)")
    ap.add_argument("--wait", type=float, default=60.0)
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
