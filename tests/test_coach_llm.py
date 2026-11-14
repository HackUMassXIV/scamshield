"""Sections 10-11: coaching escalation, LLM verdicts, alerts, Gemini second opinion, scam map."""
import asyncio
import json
import time

import httpx
from fastapi.testclient import TestClient

from cloud.bank import FakeBank
from cloud.config import CloudSettings
from cloud.gemini import Gemini
from cloud.main import create_cloud_app
from edge.alerts import Alerts
from edge.asr import FakeASR
from edge.classifier import KeywordClassifier
from edge.coach import PLAYBOOK, Coach
from edge.config import Settings
from edge.llm import LLMService, LLMVerdict, OllamaClient, parse_verdict
from edge.main import create_app
from shared.contracts import UserRecord
from shared.labels import SCAM_LABELS

from .test_edge_e2e import KEY, PUBLIC, TOKEN, collect, ring, speech_frames, start, stream, wait_for

# ------------------------------------------------------------------ playbook / coach


def test_playbook_covers_every_scam_type_with_three_steps():
    for label in [*SCAM_LABELS, "generic"]:
        steps = PLAYBOOK[label]
        assert len(steps) == 3 and all(s["say"] and s["fact"] for s in steps)
        assert steps[2]["action"] == "HANG_UP" and "action" not in steps[0]


def test_coach_escalation_timing_and_no_repeats():
    c = Coach()
    assert c.update("GREEN", "benign", 0) == (None, False)
    card, adv = c.update("YELLOW", "irs_gov", 10)
    assert adv and card.step == 1 and card.source == "playbook"
    assert c.update("YELLOW", "irs_gov", 25)[1] is False  # 15 s at YELLOW: not yet
    card, adv = c.update("YELLOW", "irs_gov", 41)  # 31 s at YELLOW -> step 2
    assert adv and card.step == 2
    card, adv = c.update("RED", "irs_gov", 45)
    assert not adv and card.step == 2  # RED just began: step 3 needs 20 s at RED
    assert c.update("RED", "irs_gov", 60)[1] is False
    card, adv = c.update("RED", "irs_gov", 66)
    assert adv and card.step == 3 and card.action == "HANG_UP"
    assert c.update("RED", "irs_gov", 200)[1] is False  # never repeats
    assert c.update("GREEN", "irs_gov", 300)[0].step == 3  # never goes back


def test_red_jumps_step1_to_step2_immediately():
    c = Coach()
    c.update("YELLOW", "bank_impersonation", 0)
    card, adv = c.update("RED", "bank_impersonation", 3)
    assert adv and card.step == 2


def test_llm_say_used_only_when_short_and_type_matches_fact_always_playbook():
    v = LLMVerdict(score=0.9, scam_type="rental_deposit", say_next="Can I see the apartment before paying?", do_now="Ask for a tour")
    c = Coach()
    card, _ = c.update("YELLOW", "rental_deposit", 0, v)
    assert card.source == "llm" and card.say == v.say_next
    assert card.fact == PLAYBOOK["rental_deposit"][0]["fact"]
    wrong = LLMVerdict(score=0.9, scam_type="irs_gov", say_next="Ask for a badge number")
    assert c.refine("rental_deposit", wrong) is None
    long = LLMVerdict(score=0.9, scam_type="rental_deposit", say_next=" ".join(["word"] * 25))
    assert c.refine("rental_deposit", long) is None
    c2 = Coach()  # the hang-up step is always scripted
    c2.update("RED", "rental_deposit", 0, v)
    card, _ = c2.update("RED", "rental_deposit", 30, v)
    assert card.step == 3 and card.source == "playbook"


def test_straight_to_red_starts_at_step_2():
    card, adv = Coach().update("RED", "irs_gov", 0)
    assert adv and card.step == 2


def test_unknown_type_uses_generic():
    card, _ = Coach().update("YELLOW", "benign", 0)
    assert card.scam_type == "generic" and card.say == PLAYBOOK["generic"][0]["say"]


# ---------------------------------------------------------------------- LLM parsing


def test_parse_verdict_drops_hallucinated_quotes_and_clamps():
    t = "Officer [NAME] says pay 600 dollars in Google Play gift cards, do not tell anyone."
    v = parse_verdict(json.dumps({"score": 92, "scam_type": "irs_gov",
                                  "red_flags": ["google play gift cards", "you will be arrested", "Do not tell anyone"],
                                  "say_next": "I'll call back.", "do_now": "Hang up"}), t)
    assert v.score == 0.92 and v.red_flags == ["google play gift cards", "do not tell anyone"] and v.dropped_flags == 1
    assert parse_verdict("not json", t) is None
    assert parse_verdict(json.dumps({"score": 0.2, "scam_type": "made_up"}), t).scam_type == "benign"


def test_ollama_request_shape_and_service_throttle():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        out = {"score": 0.8, "scam_type": "irs_gov", "red_flags": ["gift cards"], "say_next": "No.", "do_now": "Hang up"}
        return httpx.Response(200, json={"message": {"content": json.dumps(out)}})

    async def go():
        client = OllamaClient("http://ollama", "qwen2.5:3b", transport=httpx.MockTransport(handler))
        t = [0.0]
        svc = LLMService(client, min_interval=15, clock=lambda: t[0])
        got = []

        async def on_v(cid, v):
            got.append(v)

        assert not svc.request("c1", "GREEN", "buy gift cards", on_v)  # GREEN: never
        assert svc.request("c1", "YELLOW", "buy gift cards", on_v)
        assert not svc.request("c1", "RED", "buy gift cards", on_v)  # within 15 s
        t[0] = 16
        assert svc.request("c1", "RED", "buy gift cards now", on_v)
        await asyncio.sleep(0.05)
        await client.close()
        return got

    got = asyncio.run(go())
    assert len(got) >= 1 and got[-1].red_flags == ["gift cards"]
    b = seen[0]
    assert b["format"]["required"] == ["score", "scam_type", "red_flags", "say_next", "do_now"]
    assert b["options"] == {"temperature": 0.1, "num_predict": 200} and b["keep_alive"] == "30m"
    roles = [m["role"] for m in b["messages"]]
    assert roles == ["system"] + ["user", "assistant"] * 3 + ["user"]  # 3 few-shot examples


def test_newer_request_cancels_inflight():
    started, cancelled = [], []

    class Slow:
        async def verdict(self, text):
            started.append(text)
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(text)
                raise

    async def go():
        t = [0.0]
        svc = LLMService(Slow(), min_interval=15, clock=lambda: t[0])

        async def on_v(cid, v):
            pass

        svc.request("c", "YELLOW", "first", on_v)
        await asyncio.sleep(0.01)
        t[0] = 20
        svc.request("c", "RED", "second", on_v)
        await asyncio.sleep(0.01)
        svc.forget("c")
        await asyncio.sleep(0.01)

    asyncio.run(go())
    assert started == ["first", "second"] and "first" in cancelled


# --------------------------------------------------------------------------- alerts


def test_alerts_cooldowns_and_content():
    t = [1000.0]
    s = Settings(twilio_number="+14135550000", twilio_dry_run=True, alert_channel="sms", alert_cooldown_sec=20)
    a = Alerts(s, gateway=None, clock=lambda: t[0])
    u = UserRecord(id="alex", name="Alex Chen", phone="+14135550101", trusted_contact="+14135550102")
    card = {"say": "I'll call back.", "fact": "The IRS never demands gift cards.", "action": None}
    assert a.coach_step(u, card) and not a.coach_step(u, card)  # cooldown
    assert a.coach_step(u, {**card, "action": "HANG_UP"})  # hang-up always goes out
    assert a.red(u, "call1", "irs_gov") and not a.red(u, "call1", "irs_gov")  # once per call
    t[0] += 60
    assert not a.red(u, "call2", "irs_gov")  # trusted-contact cooldown (10 min)
    texts = [m["body"] for m in a.sent]
    assert "HANG UP NOW" in texts[1] and "Alex may be on a scam call" in texts[2]
    assert a.sent[2]["to"] == "+14135550102"
    wa = Alerts(Settings(twilio_number="+14135550000", twilio_dry_run=True, alert_channel="whatsapp",
                         whatsapp_from="whatsapp:+14155238886"), None)
    wa.coach_step(u, card)
    assert wa.sent[0]["to"] == "whatsapp:+14135550101" and wa.sent[0]["from"] == "whatsapp:+14155238886"
    off = Alerts(Settings(alert_channel="off", twilio_number="+1"), None)
    assert not off.coach_step(u, card)


# ------------------------------------------------------------- cloud: map + Gemini


def gemini_transport(score=0.81):
    def handler(req):
        text = json.dumps({"score": score, "scam_type": "rental_deposit", "red_flags": ["zelle me the deposit", "invented"],
                           "reason": "Deposit before viewing."})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})
    return httpx.MockTransport(handler)


def test_scam_map_seeded_and_reports():
    app = create_cloud_app(CloudSettings(api_key="ck"), bank=FakeBank())
    with TestClient(app) as c:
        h = {"X-Cloud-Key": "ck"}
        m = c.get("/api/reports/map", headers=h).json()
        assert len(m["reports"]) == 20 and m["week_counts"]
        assert all(42.3 < r["lat"] < 42.5 and -72.6 < r["lng"] < -72.4 for r in m["reports"])
        r = c.post("/api/reports", headers=h, json={"user_id": "alex", "scam_type": "immigration_uscis", "area": "Southwest"})
        assert r.status_code == 200 and "user_id" not in r.json()
        assert len(c.get("/api/reports/map", headers=h).json()["reports"]) == 21
        assert c.post("/api/reports", headers=h, json={"scam_type": "nope"}).status_code == 422


def test_second_opinion_with_gemini_and_without_key():
    g = Gemini("fake-key", "gemini-test", transport=gemini_transport())
    app = create_cloud_app(CloudSettings(api_key="ck"), bank=FakeBank(), gemini=g)
    with TestClient(app) as c:
        h = {"X-Cloud-Key": "ck"}
        with c.websocket_connect("/ws/cloud?user_id=alex&key=ck") as ws:
            ws.receive_json()
            body = {"user_id": "alex", "call_id": "c1", "local_llm": 0.62, "local_clf": 0.55,
                    "scam_type": "rental_deposit", "context": "zelle me the deposit tonight i'm out of state"}
            assert c.post("/api/second-opinion", headers=h, json=body).json() == {"queued": True}
            ev = ws.receive_json()
            assert ev["type"] == "second_opinion" and ev["local"] == 0.62 and ev["gemini"] == 0.81
            assert ev["red_flags"] == ["zelle me the deposit"]  # invented quote dropped
            assert c.post("/api/second-opinion", headers=h, json=body).json()["queued"] is False  # rate limit
    app2 = create_cloud_app(CloudSettings(api_key="ck"), bank=FakeBank())
    with TestClient(app2) as c:
        with c.websocket_connect("/ws/cloud?user_id=alex&key=ck") as ws:
            ws.receive_json()
            c.post("/api/second-opinion", headers={"X-Cloud-Key": "ck"},
                   json={"user_id": "alex", "call_id": "c2", "local_clf": 0.5, "context": "hello"})
            ev = ws.receive_json()
            assert ev["gemini"] is None and ev["status"] == "no GEMINI_API_KEY"


def test_push_subscribe_without_keys_is_harmless():
    app = create_cloud_app(CloudSettings(api_key="ck"), bank=FakeBank())
    with TestClient(app) as c:
        h = {"X-Cloud-Key": "ck"}
        assert c.get("/api/push/key", headers=h).json()["enabled"] is False
        r = c.post("/api/push/subscribe", headers=h, json={"user_id": "alex", "subscription": {"endpoint": "https://x/1", "keys": {}}})
        assert r.json()["subscriptions"] == 1


# ------------------------------------------------- full call: LLM + coach + Gemini


def test_full_call_with_llm_coach_alerts_and_second_opinion(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [{"id": "alex", "name": "Alex Chen", "phone": "+14135550101",
                                            "trusted_contact": "+14135550102"}]}))
    lines = ["Hello this is Officer Grant from USCIS about your student visa",
             "your SEVIS record is flagged and you will be deported unless you pay a fee today",
             "buy six hundred dollars in Google Play gift cards and do not tell anyone, stay on the line"]

    def ollama(req):
        out = {"score": 0.6, "scam_type": "immigration_uscis", "red_flags": ["sevis record is flagged", "fake quote"],
               "say_next": "I'll ask my international office first.", "do_now": "Hang up"}
        return httpx.Response(200, json={"message": {"content": json.dumps(out)}})

    cloud_app = create_cloud_app(CloudSettings(api_key="ck"), bank=FakeBank(),
                                 gemini=Gemini("k", "m", transport=gemini_transport(0.9)))
    with TestClient(cloud_app):
        s = Settings(public_base_url=PUBLIC, twilio_auth_token=TOKEN, api_key=KEY, users_file=str(users),
                     asr_backend="fake", vad_backend="energy", twilio_dry_run=True, twilio_number="+14135550000",
                     cloud_url="http://cloud", cloud_api_key="ck", llm_min_interval=0)
        llm = LLMService(OllamaClient("http://ollama", "m", transport=httpx.MockTransport(ollama)), min_interval=0)
        edge_app = create_app(s, asr_backend=FakeASR(lines, cycle=False), classifier=KeywordClassifier())
        with TestClient(edge_app) as edge:
            rt = edge_app.state.rt
            rt.llm = llm
            rt.cloud._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud_app),
                                                 base_url="http://cloud", headers={"X-Cloud-Key": "ck"})
            ev = collect(edge_app)
            ring(edge)
            call_id = start(edge).json()["call_id"]
            with edge.websocket_connect("/ws/twilio") as ws:
                stream(ws, call_id, speech_frames())
            wait_for(ev, "summary")
            t0 = time.time()
            while (rt.cloud._tasks or not cloud_app.state.cs.second) and time.time() - t0 < 5:
                time.sleep(0.05)
            coach = [e for e in ev if e["type"] == "coach"]
            llm_ev = [e for e in ev if e["type"] == "llm"]
            risks = [e for e in ev if e["type"] == "risk"]
            summary = wait_for(ev, "summary")[0]
            assert coach and coach[0]["fact"] == PLAYBOOK["immigration_uscis"][coach[0]["step"] - 1]["fact"]
            assert any(c["source"] == "llm" and c["say"].startswith("I'll ask my international office") for c in coach)
            assert llm_ev and llm_ev[0]["red_flags"] == ["sevis record is flagged"] and llm_ev[0]["dropped_flags"] == 1
            assert risks[-1]["coach"] is not None and risks[-1]["level"] == "RED"
            assert summary["max_level"] == "RED" and summary["coach_steps"] >= 2
            kinds = [m["kind"] for m in rt.alerts.sent]
            assert "coach" in kinds and kinds.count("trusted") == 1
            assert all("deported" not in m["body"] for m in rt.alerts.sent)  # no transcript in SMS
            so = list(cloud_app.state.cs.second.values())[0]
            assert so["local_llm"] == 0.6 and so["gemini"] == 0.9


def test_app_config_endpoint(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [{"id": "alex", "name": "Alex Chen", "phone": "+14135550101"}]}))
    app = create_app(Settings(users_file=str(users), asr_backend="fake", vad_backend="energy",
                              twilio_number="+14135550000"), asr_backend=FakeASR([]), classifier=KeywordClassifier())
    with TestClient(app) as c:
        cfg = c.get("/api/app-config").json()
        assert cfg["guardian_number"] == "+14135550000" and cfg["users"][0]["name"] == "Alex Chen"
        assert c.get("/cloud/health").status_code == 503  # no CLOUD_URL configured
