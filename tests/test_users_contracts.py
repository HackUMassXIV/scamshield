import json
import os
import time

import pytest

from edge.config import load_settings
from edge.users import UserDirectory, normalize_phone
from shared.contracts import Hold, RiskEvent, Transfer


def test_normalize_phone():
    assert normalize_phone("(413) 555-0101") == "+14135550101"
    assert normalize_phone("1 413 555 0101") == "+14135550101"
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert normalize_phone("123") is None and normalize_phone(None) is None


def test_directory_lookup_and_hot_reload(tmp_path):
    p = tmp_path / "users.json"
    p.write_text(json.dumps({"users": [{"id": "a", "name": "Ann Lee", "phone": "4135550101"}]}))
    d = UserDirectory(str(p))
    assert d.by_phone("+1 (413) 555-0101").id == "a"
    assert d.by_phone("+19998887777") is None
    p.write_text(json.dumps({"users": [{"id": "b", "name": "Bo Li", "phone": "+14135550102"}]}))
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert d.by_phone("4135550102").id == "b"
    assert UserDirectory.name_tokens(d.by_id("b")) == ["Bo Li"]


def test_shipped_users_file_is_valid():
    d = UserDirectory(load_settings({}).users_file)
    assert d.by_id("alex").phone == "+14135550101"


def test_settings_derive_ws_url():
    s = load_settings({"PUBLIC_BASE_URL": "https://abc.trycloudflare.com/"})
    assert s.ws_base_url == "wss://abc.trycloudflare.com"
    assert load_settings({"FAKE_ASR_LINES": '["x","y"]'}).fake_asr_lines == ("x", "y")


def test_contracts_roundtrip():
    ev = RiskEvent(call_id="c", user_id="u", level="RED", score=81.2, scam_type="immigration_uscis", features={"clf": 0.9})
    assert RiskEvent.model_validate_json(ev.model_dump_json()).level == "RED"
    with pytest.raises(Exception):
        RiskEvent(call_id="c", user_id="u", level="PURPLE", score=1, scam_type="x")
    h = Hold(transfer=Transfer(user_id="u", payee_id="p", amount=600))
    assert h.status == "HELD" and h.id.startswith("hold_")
