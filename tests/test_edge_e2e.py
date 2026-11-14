"""End-to-end tests of sections 5-6 with the real FastAPI app.

Covers: signed Twilio webhook -> silent leg -> PWA start -> disclosure TwiML
(Say before Start) -> media websocket with mu-law frames -> VAD -> ASR (scripted)
-> redaction -> /ws/ui events -> call_ended, plus every rejection path.
"""
import base64
import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from edge.asr import FakeASR
from edge.classifier import KeywordClassifier
from edge.config import Settings
from edge.main import create_app

from .helpers import timeline, to_mulaw_frames

TOKEN = "test_auth_token"
PUBLIC = "https://edge.test"
KEY = "k3y"
ALEX = "+14135550101"

SCRIPT = [
    "Hello Alex Student, this is Officer James Miller from USCIS",
    "your social 123-45-6789 is suspended, pay $600 in gift cards today",
    "call me back at 413-555-0199 and do not tell anyone",
]


class FakeTwilio:
    """Stands in for twilio.rest.Client: records calls(sid).update(...)."""

    def __init__(self):
        self.updates = []

    def calls(self, sid):
        outer = self

        class _Call:
            def update(self, **kw):
                outer.updates.append({"sid": sid, **kw})

        return _Call()


def make(tmp_path, **over):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [
        {"id": "alex", "name": "Alex Student", "phone": ALEX, "aliases": ["Alex"]},
        {"id": "sam", "name": "Sam Rivera", "phone": "+14135550103"},
    ]}))
    s = Settings(
        public_base_url=PUBLIC,
        twilio_auth_token=TOKEN,
        api_key=KEY,
        users_file=str(users),
        asr_backend="fake",
        vad_backend="energy",
        **over,
    )
    tw = FakeTwilio()
    app = create_app(s, asr_backend=FakeASR(SCRIPT, cycle=False), gateway_client=tw,
                     classifier=KeywordClassifier())  # deterministic: no trained model needed
    return app, tw


def sign(path, params):
    return RequestValidator(TOKEN).compute_signature(PUBLIC + path, params)


def ring(client, frm=ALEX, sid="CA" + "1" * 32, path="/twilio/voice", sig=None, query=""):
    p = {"CallSid": sid, "From": frm, "To": "+14135559999", "CallStatus": "ringing"}
    url = path + (f"?{query}" if query else "")
    s = sig if sig is not None else sign(url, p)
    return client.post(url, data=p, headers={"X-Twilio-Signature": s})


def start(client, user="alex", key=KEY):
    return client.post("/api/screen/start", json={"user_id": user}, headers={"X-Guardian-Key": key})


def collect(app):
    events = []
    app.state.rt.hub.add_listener(events.append)
    return events


def wait_for(events, typ, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hits = [e for e in events if e["type"] == typ]
        if hits:
            return hits
        time.sleep(0.02)
    raise AssertionError(f"no {typ!r} event; got {[e['type'] for e in events]}")


def speech_frames():
    """3 utterances of 'speech' separated by pauses, as Twilio mu-law frames."""
    x8 = timeline([("silence", 0.5), ("speech", 1.5), ("silence", 0.8), ("speech", 1.8),
                   ("silence", 0.8), ("speech", 1.2), ("silence", 1.0)], sr=8000)
    return to_mulaw_frames(x8)


def stream(ws, call_id, frames, call_sid="CA" + "1" * 32, tracks=("inbound",), track="inbound"):
    ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
    ws.send_json({"event": "start", "streamSid": "MZ1", "start": {
        "streamSid": "MZ1", "callSid": call_sid, "tracks": list(tracks),
        "customParameters": {"call_id": call_id},
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
    for i, f in enumerate(frames):
        ws.send_json({"event": "media", "streamSid": "MZ1", "media": {
            "track": track, "chunk": str(i), "payload": base64.b64encode(f).decode()}})
    ws.send_json({"event": "stop", "streamSid": "MZ1"})


# --------------------------------------------------------------------- happy path
def test_full_merge_flow_transcribes_and_redacts(tmp_path):
    app, tw = make(tmp_path)
    with TestClient(app) as c:
        ev = collect(app)
        r = ring(c)
        assert r.status_code == 200
        assert "<Pause" in r.text and "<Stream" not in r.text  # nothing streamed before disclosure
        assert wait_for(ev, "leg_connected")[0]["user_id"] == "alex"

        r = start(c)
        assert r.status_code == 200
        call_id = r.json()["call_id"]
        assert len(call_id) >= 20  # unguessable token binds the stream

        # Twilio received disclosure-then-stream TwiML for the right call
        upd = tw.updates[-1]
        assert upd["sid"] == "CA" + "1" * 32
        twiml = upd["twiml"]
        assert twiml.index("<Say>") < twiml.index("<Start>")
        assert "screened by an automated scam filter" in twiml
        assert 'url="wss://edge.test/ws/twilio"' in twiml and 'track="inbound_track"' in twiml
        assert f'name="call_id" value="{call_id}"' in twiml

        with c.websocket_connect("/ws/twilio") as ws:
            stream(ws, call_id, speech_frames())

        ended = wait_for(ev, "call_ended")[0]
        tx = [e for e in ev if e["type"] == "transcript"]
        assert [e["call_id"] for e in tx] == [call_id] * 3
        texts = [e["text"] for e in tx]
        # PII is gone; scam vocabulary is kept for the classifier
        assert texts[0] == "Hello [NAME], this is Officer [NAME] from USCIS"
        assert "[SSN]" in texts[1] and "123-45-6789" not in texts[1] and "gift cards" in texts[1]
        assert "[PHONE]" in texts[2]
        assert all("alex" not in t.lower() for t in texts)
        assert tx[0]["redactions"]["NAME"] == 2
        assert all(e["speaker"] == "mixed" for e in tx)
        assert all("asr_ms" in e["latency"] and "queue_ms" in e["latency"] for e in tx)
        assert tx[0]["t_start"] < tx[1]["t_start"] < tx[2]["t_start"]
        summ = ended["summary"]
        assert summ["utterances"] == 3 and summ["redactions"]["NAME"] >= 2
        assert ended["reason"] == "stream_stop"
        assert app.state.rt.sessions.all() == []  # cleaned up
        order = [e["type"] for e in ev]
        assert order.index("leg_connected") < order.index("screening_requested") \
            < order.index("screening_started") < order.index("transcript") < order.index("call_ended")


def test_transcript_hooks_receive_redacted_events(tmp_path):
    app, _ = make(tmp_path)
    seen = []

    async def hook(session, ev):
        seen.append((ev["text"], session.recent_text(2)))

    with TestClient(app) as c:
        app.state.rt.transcript_hooks.append(hook)
        ev = collect(app)
        ring(c)
        call_id = start(c).json()["call_id"]
        with c.websocket_connect("/ws/twilio") as ws:
            stream(ws, call_id, speech_frames())
        wait_for(ev, "call_ended")
    assert len(seen) == 3
    assert "[SSN]" in seen[1][0] and seen[1][1].startswith("Hello [NAME]")


def test_ui_websocket_gets_events_and_replay(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        ring(c)
        with c.websocket_connect(f"/ws/ui?user_id=alex&key={KEY}") as ui:
            first = ui.receive_json()  # replayed history
            assert first["type"] == "leg_connected" and first["replay"] is True
            start(c)
            live = ui.receive_json()
            assert live["type"] == "screening_requested" and "replay" not in live


def test_bridge_mode_separates_caller_and_student(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        ev = collect(app)
        r = ring(c, frm="+12025550000", path="/twilio/bridge", query="user=alex")
        assert r.status_code == 200
        assert r.text.index("<Say>") < r.text.index("<Start>")
        assert 'track="both_tracks"' in r.text and f"<Dial>{ALEX}</Dial>" in r.text
        call_id = r.text.split('name="call_id" value="')[1].split('"')[0]
        with c.websocket_connect("/ws/twilio") as ws:
            stream(ws, call_id, speech_frames(), tracks=("inbound", "outbound"), track="inbound")
        wait_for(ev, "call_ended")
        tx = [e for e in ev if e["type"] == "transcript"]
        assert tx and all(e["speaker"] == "caller" for e in tx)
        assert [e for e in ev if e["type"] == "screening_started"][0]["mode"] == "bridge"


def test_local_audio_websocket_uses_same_pipeline(tmp_path):
    app, _ = make(tmp_path)
    x16 = timeline([("silence", 0.5), ("speech", 1.5), ("silence", 1.0)])
    pcm = (x16 * 32767).astype(np.int16)
    with TestClient(app) as c:
        ev = collect(app)
        with c.websocket_connect(f"/ws/audio?user_id=alex&key={KEY}") as ws:
            assert "call_id" in ws.receive_json()
            for i in range(0, len(pcm), 320):
                ws.send_bytes(pcm[i : i + 320].tobytes())
            ws.send_text("stop")
            assert ws.receive_json()["ended"] is True
        wait_for(ev, "call_ended")
        tx = [e for e in ev if e["type"] == "transcript"]
        assert len(tx) == 1 and tx[0]["text"].startswith("Hello [NAME]")


# --------------------------------------------------------------- rejection paths
def test_bad_or_missing_signature_is_rejected(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        assert ring(c, sig="forged").status_code == 403
        assert ring(c, sig="").status_code == 403
        assert app.state.rt.sessions.all() == []


def test_signature_valid_for_forwarded_host(tmp_path):
    """Behind a tunnel Twilio may sign the forwarded host instead of PUBLIC_BASE_URL."""
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        p = {"CallSid": "CA9", "From": ALEX, "To": "+1", "CallStatus": "ringing"}
        sig = RequestValidator(TOKEN).compute_signature("https://other.trycloudflare.com/twilio/voice", p)
        r = c.post("/twilio/voice", data=p, headers={
            "X-Twilio-Signature": sig, "X-Forwarded-Host": "other.trycloudflare.com", "X-Forwarded-Proto": "https"})
        assert r.status_code == 200


def test_unregistered_caller_is_hung_up(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        r = ring(c, frm="+12025550000")
        assert r.status_code == 200 and "<Hangup" in r.text and "not registered" in r.text
        assert app.state.rt.sessions.all() == []


def test_start_requires_key_and_a_connected_leg(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        assert start(c).status_code == 409  # no leg yet
        ring(c)
        assert start(c, key="wrong").status_code == 401
        assert start(c).status_code == 200
        assert start(c).status_code == 200  # idempotent


def test_stream_rejected_before_start_and_with_wrong_call_id(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        ev = collect(app)
        ring(c)
        sess = app.state.rt.sessions.active("alex")
        # stream before the student pressed Start (no disclosure yet) -> rejected
        with c.websocket_connect("/ws/twilio") as ws:
            stream(ws, sess.id, speech_frames()[:5])
        assert sess.state == "leg_connected"
        start(c)
        with c.websocket_connect("/ws/twilio") as ws:  # forged call_id
            stream(ws, "guess", speech_frames()[:5])
        with c.websocket_connect("/ws/twilio") as ws:  # right id, different call
            stream(ws, sess.id, speech_frames()[:5], call_sid="CA" + "2" * 32)
        time.sleep(0.2)
        assert not [e for e in ev if e["type"] in {"transcript", "screening_started"}]


def test_ui_ws_requires_key_and_known_user(tmp_path):
    app, _ = make(tmp_path)
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as c:
        for q in ("user_id=alex&key=bad", f"user_id=nobody&key={KEY}"):
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(f"/ws/ui?{q}") as ws:
                    ws.receive_json()


def test_leg_timeout_hangs_up_without_streaming(tmp_path):
    app, tw = make(tmp_path, leg_timeout_sec=1)
    with TestClient(app) as c:
        ev = collect(app)
        r = ring(c)
        assert 'length="1"' in r.text
        wait_for(ev, "leg_timeout", timeout=5)
        ended = wait_for(ev, "call_ended", timeout=5)[0]
        assert ended["reason"] == "leg_timeout"
        assert {"sid": "CA" + "1" * 32, "status": "completed"} in tw.updates
        assert app.state.rt.sessions.all() == []


def test_status_callback_and_stop_end_the_session(tmp_path):
    app, tw = make(tmp_path)
    with TestClient(app) as c:
        ev = collect(app)
        ring(c)
        p = {"CallSid": "CA" + "1" * 32, "CallStatus": "completed"}
        r = c.post("/twilio/status", data=p, headers={"X-Twilio-Signature": sign("/twilio/status", p)})
        assert r.status_code == 204
        assert wait_for(ev, "call_ended")[0]["reason"] == "call_completed"
        ring(c, sid="CA" + "3" * 32)
        r = c.post("/api/screen/stop", json={"user_id": "alex"}, headers={"X-Guardian-Key": KEY})
        assert r.json() == {"state": "ended"}
        assert {"sid": "CA" + "3" * 32, "status": "completed"} in tw.updates


def test_new_leg_replaces_old_session(tmp_path):
    app, tw = make(tmp_path)
    with TestClient(app) as c:
        ring(c, sid="CA" + "a" * 32)
        first = app.state.rt.sessions.active("alex")
        ring(c, sid="CA" + "b" * 32)
        assert app.state.rt.sessions.active("alex") is not first
        assert {"sid": "CA" + "a" * 32, "status": "completed"} in tw.updates
        assert len(app.state.rt.sessions.all()) == 1


def test_dropped_stream_finishes_after_grace(tmp_path):
    app, _ = make(tmp_path, stream_grace_sec=1)
    with TestClient(app) as c:
        ev = collect(app)
        ring(c)
        call_id = start(c).json()["call_id"]
        with c.websocket_connect("/ws/twilio") as ws:  # close without "stop"
            ws.send_json({"event": "start", "start": {"callSid": "CA" + "1" * 32, "tracks": ["inbound"],
                                                      "customParameters": {"call_id": call_id}}})
        time.sleep(0.3)
        assert not [e for e in ev if e["type"] == "call_ended"]  # still in grace period
        assert wait_for(ev, "call_ended", timeout=5)[0]["reason"] == "stream_lost"


def test_health_and_dry_run(tmp_path):
    app, tw = make(tmp_path, twilio_dry_run=True)
    with TestClient(app) as c:
        h = c.get("/health").json()
        assert h["ok"] and h["asr"] == "fake" and h["vad"] == "energy" and h["users"] == ["alex", "sam"]
        ring(c)
        start(c)
        assert tw.updates == []  # nothing sent to Twilio in dry-run
        assert "<Say>" in app.state.rt.gateway.dry_run_log[-1]["twiml"]
