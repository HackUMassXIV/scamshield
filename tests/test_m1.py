"""Milestone M1, in one process: simulated Twilio scam call -> edge VAD/ASR/redaction
-> classifier + risk engine -> RED -> redacted RiskEvent to the cloud -> a $600
transfer to GiftCardDepot is HELD. Plus the control: a benign call stays GREEN.
"""
import json
import time

import httpx
from fastapi.testclient import TestClient

from cloud.bank import FakeBank
from cloud.config import CloudSettings
from cloud.main import create_cloud_app
from edge.asr import FakeASR
from edge.classifier import KeywordClassifier
from edge.config import Settings
from edge.main import create_app

from .test_edge_e2e import PUBLIC, TOKEN, KEY, collect, ring, speech_frames, start, stream, wait_for

SCAM = ["Hello this is Officer Grant from USCIS about your student visa",
        "your SEVIS record is flagged and you will be deported unless you pay a fee today",
        "buy six hundred dollars in Google Play gift cards and do not tell anyone, stay on the line"]
BENIGN = ["Hi this is the campus pharmacy calling",
          "your prescription is ready for pickup at the health center",
          "we are open until six and you can pay with your insurance card"]


def run_call(tmp_path, lines):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [{"id": "alex", "name": "Alex Chen", "phone": "+14135550101"}]}))
    cloud_app = create_cloud_app(CloudSettings(api_key="ck", cooldown_sec=60), bank=FakeBank())
    with TestClient(cloud_app) as cloud:
        s = Settings(public_base_url=PUBLIC, twilio_auth_token=TOKEN, api_key=KEY, users_file=str(users),
                     asr_backend="fake", vad_backend="energy", twilio_dry_run=True,
                     cloud_url="http://cloud", cloud_api_key="ck")
        edge_app = create_app(s, asr_backend=FakeASR(lines, cycle=False), classifier=KeywordClassifier())
        with TestClient(edge_app) as edge:
            rt = edge_app.state.rt
            # route the edge's cloud client to the in-process cloud app
            rt.cloud._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud_app),
                                                 base_url="http://cloud", headers={"X-Cloud-Key": "ck"})
            ev = collect(edge_app)
            ring(edge)
            call_id = start(edge).json()["call_id"]
            with edge.websocket_connect("/ws/twilio") as ws:
                stream(ws, call_id, speech_frames())
            wait_for(ev, "call_ended")
            t0 = time.time()
            while rt.cloud._tasks and time.time() - t0 < 5:
                time.sleep(0.05)
            shield = cloud.get("/api/shield/alex", headers={"X-Cloud-Key": "ck"}).json()
            pay = cloud.post("/api/transfer", headers={"X-Cloud-Key": "ck"}, json={
                "user_id": "alex", "payee_id": "giftcarddepot", "amount": 600, "memo": "fee"}).json()
            rent = cloud.post("/api/transfer", headers={"X-Cloud-Key": "ck"}, json={
                "user_id": "alex", "payee_id": "landlord", "amount": 900, "memo": "rent"}).json()
            return [e for e in ev if e["type"] == "risk"], shield, pay, rent, rt.cloud


def test_m1_scam_call_goes_red_and_payment_is_held(tmp_path):
    risks, shield, pay, rent, cloud = run_call(tmp_path, SCAM)
    assert [r["level"] for r in risks][-1] == "RED"
    assert risks[-1]["scam_type"] == "immigration_uscis"
    assert any("gift card" in f for f in risks[-1]["red_flags"])
    assert cloud.sent >= 3 and cloud.failed == 0
    assert shield["last_red_at"] > 0 and shield["red_scam_type"] == "immigration_uscis"
    assert shield["session_active"] is False  # session-end reached the cloud
    assert pay["status"] == "HELD"
    assert any("Scam call flagged" in r for r in pay["reasons"])
    assert rent["status"] == "HELD"  # even rent waits during the hour after a RED call


def test_m1_control_benign_call_stays_green_and_payments_flow(tmp_path):
    risks, shield, pay, rent, _ = run_call(tmp_path, BENIGN)
    assert {r["level"] for r in risks} == {"GREEN"}
    assert shield["last_red_at"] == 0
    assert pay["status"] == "HELD"  # new gift-card payee + unusual amount is held anyway
    assert rent["status"] == "OK"


def test_edge_keeps_working_when_cloud_is_down(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [{"id": "alex", "name": "Alex Chen", "phone": "+14135550101"}]}))
    s = Settings(public_base_url=PUBLIC, twilio_auth_token=TOKEN, api_key=KEY, users_file=str(users),
                 asr_backend="fake", vad_backend="energy", twilio_dry_run=True, cloud_url="http://127.0.0.1:9")
    app = create_app(s, asr_backend=FakeASR(SCAM, cycle=False), classifier=KeywordClassifier())
    with TestClient(app) as edge:
        ev = collect(app)
        ring(edge)
        call_id = start(edge).json()["call_id"]
        with edge.websocket_connect("/ws/twilio") as ws:
            stream(ws, call_id, speech_frames())
        wait_for(ev, "call_ended")
        assert [e["level"] for e in ev if e["type"] == "risk"][-1] == "RED"  # offline still turns red


def test_im_suspicious_button_raises_live_call(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [{"id": "alex", "name": "Alex Chen", "phone": "+14135550101"}]}))
    s = Settings(public_base_url=PUBLIC, twilio_auth_token=TOKEN, api_key=KEY, users_file=str(users),
                 asr_backend="fake", vad_backend="energy", twilio_dry_run=True)
    app = create_app(s, asr_backend=FakeASR(BENIGN, cycle=False), classifier=KeywordClassifier())
    with TestClient(app) as edge:
        ev = collect(app)
        r = edge.post("/api/flag", json={"user_id": "alex"}, headers={"X-Guardian-Key": KEY})
        assert r.status_code == 200
        ring(edge)
        call_id = start(edge).json()["call_id"]
        with edge.websocket_connect("/ws/twilio") as ws:
            stream(ws, call_id, speech_frames())
        wait_for(ev, "call_ended")
        assert {e["level"] for e in ev if e["type"] == "risk"} == {"YELLOW"}  # floor while flagged
        assert wait_for(ev, "user_flag")
