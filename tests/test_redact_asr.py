import numpy as np
import pytest

from edge.asr import FakeASR, clean_text
from edge.redact import Redactor, luhn_ok, redact


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("my social is 123-45-6789", "my social is [SSN]"),
        ("my social is 123 45 6789 ok", "my social is [SSN] ok"),
        ("ssn 123456789", "ssn [SSN]"),
        ("card 4111 1111 1111 1111 expires", "card [CARD] expires"),
        ("card 4111-1111-1111-1111", "card [CARD]"),
        ("call (413) 555-0123 now", "call [PHONE] now"),
        ("call 413.555.0123 now", "call [PHONE] now"),
        ("call 1-800-829-1040 now", "call [PHONE] now"),
        ("call +1 413 555 0123", "call [PHONE]"),
        ("routing 123456789012", "routing [ACCOUNT]"),
        ("write bob.smith+x@umass.edu please", "write [EMAIL] please"),
        ("one two three four five six seven eight nine", "[NUMBER]"),
        ("my pin is one two three four", "my pin is one two three four"),  # too short
    ],
)
def test_digit_like_pii(raw, expected):
    assert redact(raw).text == expected


def test_non_luhn_16_digits_is_account_not_card():
    r = redact("number 1234 5678 9012 3456")
    assert "[ACCOUNT]" in r.text and "[CARD]" not in r.text
    assert luhn_ok("4111111111111111") and not luhn_ok("1234567890123456")


def test_amounts_and_scam_vocabulary_survive():
    t = "the IRS needs $600 in gift cards, do not tell anyone, wire it today"
    assert redact(t).text == t  # the classifier needs these features


def test_names_by_cue_but_not_organizations():
    r = Redactor()
    assert r.redact("this is Officer James Miller from the IRS").text == "this is Officer [NAME] from the IRS"
    assert r.redact("my name is Priya Nair and I work at the bank").text == "my name is [NAME] and I work at the bank"
    assert r.redact("this is the IRS calling").text == "this is the IRS calling"
    assert r.redact("this is Immigration calling about your visa").text == "this is Immigration calling about your visa"
    assert r.redact("Hello Priya, please hold").text == "Hello [NAME], please hold"


def test_students_own_name_is_scrubbed_everywhere():
    r = Redactor(["Alex Student", "Alex"])
    out = r.redact("Am I speaking with alex student? We owe alex a refund")
    assert "alex" not in out.text.lower()
    assert out.counts["NAME"] >= 2


def test_counts_and_idempotence():
    r = redact("ssn 123-45-6789 phone 413-555-0123")
    assert r.counts == {"SSN": 1, "PHONE": 1} and r.total == 2
    assert redact(r.text).text == r.text
    assert redact("").text == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" Thanks for watching! ", ""),
        ("[MUSIC]", ""),
        ("(silence)", ""),
        ("you", ""),
        ("pay pay pay pay pay pay now", "pay now"),
        ("This is the IRS.", "This is the IRS."),
    ],
)
def test_whisper_hallucination_filter(raw, expected):
    assert clean_text(raw) == expected


def test_prompt_echo_is_dropped():
    p = "IRS, USCIS, SEVIS, visa"
    assert clean_text("IRS USCIS SEVIS visa", p) == ""


def test_fake_asr_ignores_silence_and_cycles():
    f = FakeASR(["a", "b"])
    loud = np.full(1600, 0.2, np.float32)
    assert f.transcribe(np.zeros(16000, np.float32)) == ""
    assert [f.transcribe(loud) for _ in range(3)] == ["a", "b", "a"]
