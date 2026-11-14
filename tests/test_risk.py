from edge.keywords import comply_score, keyword_label_probs, kw_hits, kw_score
from edge.risk import CallRisk, Shield, band, raw_score
from shared.textnorm import normalize_for_clf


def test_raw_score_uses_only_available_features():
    # LLM missing -> renormalized over clf + kw, so it isn't penalized
    assert raw_score({"clf": 1.0, "kw": 1.0, "llm": None}) == 100
    assert abs(raw_score({"clf": 0.5, "kw": 0.0, "llm": None}) - 100 * 0.3 / 0.85) < 1e-9
    assert raw_score({"clf": 1.0, "kw": 1.0, "llm": 0.0}) == 85
    assert raw_score({"clf": 0.5, "kw": 0.5, "voice": 1, "comply": 1}) == 70  # +10 +10 bonuses
    assert raw_score({}) == 0


def test_shield_dead_zone_and_bands():
    sh = Shield()
    sh.update(2, 0.9, 0)  # within the dead zone: nothing moves
    assert sh.s == 0
    assert [band(x) for x in (0, 39.9, 40, 69.9, 70)] == [0, 0, 1, 1, 2]


def test_shield_strong_evidence_jumps_straight_to_red():
    sh = Shield()
    assert sh.update(90, 0.95, 0)[0] == "RED"


def test_shield_moderate_evidence_climbs_over_several_utterances():
    sh = Shield()
    levels = [sh.update(78, 0.9, t)[0] for t in range(0, 20, 4)]
    assert levels[0] == "GREEN" and "YELLOW" in levels and levels[-1] == "RED"


def test_low_confidence_moves_slower():
    hi, lo = Shield(), Shield()
    hi.update(60, 0.9, 0)
    lo.update(60, 0.4, 0)
    assert hi.s > lo.s > 0


def test_red_latches_60s_then_steps_down_one_level_at_a_time():
    sh = Shield()
    sh.update(95, 0.95, 0)
    assert sh.update(0, 0.95, 30)[0] == "RED"  # latched
    assert sh.update(0, 0.95, 59)[0] == "RED"
    for t in range(60, 200, 4):
        lv, _ = sh.update(0, 0.95, t)
        if lv != "RED":
            break
    assert lv == "YELLOW"  # never jumps RED -> GREEN
    for t2 in range(t, t + 200, 4):
        lv, _ = sh.update(0, 0.95, t2)
    assert lv == "GREEN"


def test_flag_floor_is_yellow():
    sh = Shield()
    assert sh.update(0, 0.9, 0, flagged=True)[0] == "YELLOW"


def test_keywords_and_negation_guard():
    n = normalize_for_clf("We will never ask for your PIN or a one time code over the phone.")
    assert kw_hits(n) == []
    n = normalize_for_clf("Read me the one time code. Buy Google Play gift cards and don't tell anyone!")
    hits = [h for h, _ in kw_hits(n)]
    assert any("gift card" in h for h in hits) and any("tell" in h for h in hits) and any("one time code" in h for h in hits)
    assert kw_score(kw_hits(n)) > 0.9
    assert comply_score("okay how many cards do i need") == 1.0
    p = keyword_label_probs(normalize_for_clf("download anydesk so i can access your computer"))
    scam_only = {k: v for k, v in p.items() if k != "benign"}
    assert max(scam_only, key=scam_only.get) == "tech_support"  # one hit = weak evidence, but typed right
    assert p["benign"] < 1.0


def test_textnorm_matches_training_format():
    assert normalize_for_clf("Pay $1,200 NOW, Officer [NAME] said.") == "pay 1200 dollars now officer [name] said"
    assert normalize_for_clf("I'm at CVS — what's the code?") == "i'm at cvs what's the code"


class FakeClf:
    def __init__(self, table):
        self.table = table

    def predict(self, norm):
        for k, v in self.table.items():
            if k in norm:
                return v
        return {"benign": 0.9, "irs_gov": 0.1}


def test_callrisk_short_windows_carry_less_evidence_and_type_is_sticky():
    cr = CallRisk()
    f, _ = cr.features({"benign": 0.0, "irs_gov": 1.0}, "okay")
    assert f["clf"] < 0.2 and f["p_scam"] == 1.0
    for i in range(3):
        r = cr.step({"benign": 0.05, "irs_gov": 0.9, "bank_impersonation": 0.05},
                    "this is the irs you owe back taxes pay with gift cards today", now=i * 4)
    assert r["level"] == "RED" and r["scam_type"] == "irs_gov" and cr.max_level == "RED"
    assert "gift cards" in cr.red_flags[0]


def test_callrisk_ablation_drop():
    cr = CallRisk(drop=frozenset({"kw"}))
    f, _ = cr.features({"benign": 0.5}, "gift cards gift cards")
    assert f["kw"] is None


def test_conditional_threats_are_not_treated_as_negations():
    # regression: found on a real Whisper run ("If this is not resolved today, a warrant...")
    for text, must in [("If this is not resolved today, a warrant will be issued.", "warrant"),
                       ("If you do not pay today, police will come to your address.", "police will"),
                       ("If you don't buy the gift cards today you will be arrested.", "gift cards")]:
        assert must in [h for h, _ in kw_hits(normalize_for_clf(text))], text
    assert kw_hits(normalize_for_clf("There is no need to pay with gift cards, it is free.")) == []
