import numpy as np

from edge.audio.codec import pcm16_to_ulaw, ulaw_to_pcm16
from edge.audio.resample import Upsampler2x
from edge.audio.vad import EnergyVAD, Segmenter

from .helpers import timeline

# Reference G.711 mu-law decode values (ITU-T G.711 / audioop.ulaw2lin):
# code 0x00 -> -32124, 0x7F -> 0, 0x80 -> 32124, 0xFF -> 0, 0x0F -> -16764, 0x8F -> 16764
REF = {0x00: -32124, 0x0F: -16764, 0x7F: 0, 0x80: 32124, 0x8F: 16764, 0xFF: 0}


def test_ulaw_decode_matches_g711_reference():
    out = ulaw_to_pcm16(bytes(REF.keys()))
    assert out.tolist() == list(REF.values())


def test_ulaw_decode_is_odd_symmetric_and_monotone():
    neg = ulaw_to_pcm16(bytes(range(0, 128))).astype(int)
    pos = ulaw_to_pcm16(bytes(range(128, 256))).astype(int)
    assert np.array_equal(neg, -pos)
    assert np.all(np.diff(pos) <= 0)  # 0x80 is loudest positive, 0xFF is zero


def test_ulaw_roundtrip_error_is_within_companding_step():
    x = (np.sin(2 * np.pi * 440 * np.arange(8000) / 8000) * 20000).astype(np.int16)
    y = ulaw_to_pcm16(pcm16_to_ulaw(x))
    err = np.abs(x.astype(int) - y.astype(int))
    assert np.all(err <= np.maximum(0.04 * np.abs(x.astype(int)), 40))
    # encoding the decoded value again must give the same code (codebook is stable)
    codes = pcm16_to_ulaw(x)
    assert pcm16_to_ulaw(ulaw_to_pcm16(codes)) == codes


def test_upsampler_preserves_tone_and_is_chunk_invariant():
    sr = 8000
    x = (0.5 * np.sin(2 * np.pi * 1000 * np.arange(sr) / sr)).astype(np.float32)
    whole = Upsampler2x().process(x)
    chunks = Upsampler2x()
    parts = np.concatenate([chunks.process(x[i : i + 160]) for i in range(0, len(x), 160)])
    assert whole.shape == (2 * sr,)
    assert np.allclose(whole, parts, atol=1e-6)  # no artifacts at 20 ms frame boundaries
    spec = np.abs(np.fft.rfft(whole[4000:12000]))
    assert abs(np.argmax(spec) * 16000 / 8000 - 1000) < 5  # still 1 kHz
    rms = np.sqrt(np.mean(whole[2000:-2000] ** 2))
    assert 0.33 < rms < 0.37  # amplitude preserved (0.5/sqrt2 = 0.354)


def test_segmenter_splits_on_pauses():
    x = timeline([("silence", 0.6), ("speech", 1.5), ("silence", 1.0), ("speech", 2.0), ("silence", 1.0)])
    seg = Segmenter("mixed", EnergyVAD())
    utts = []
    for i in range(0, len(x), 320):  # 20 ms pushes like Twilio
        utts += seg.push(x[i : i + 320])
    utts += seg.flush()
    assert len(utts) == 2
    assert 1.4 < utts[0].end_s - utts[0].start_s < 2.1
    assert 1.9 < utts[1].end_s - utts[1].start_s < 2.6
    assert 0.3 < utts[0].start_s < 0.6  # ~200 ms preroll before speech at 0.6 s
    assert utts[1].start_s > utts[0].end_s


def test_segmenter_force_cuts_long_speech_without_losing_audio():
    x = timeline([("silence", 0.3), ("speech", 14.0), ("silence", 1.0)])
    seg = Segmenter("mixed", EnergyVAD(), max_utt_s=6.0)
    utts = seg.push(x) + seg.flush()
    assert len(utts) >= 3
    assert all(u.audio.size / 16000 <= 6.3 for u in utts)
    total = sum(u.audio.size for u in utts) / 16000
    assert total > 13.0  # essentially all 14 s of speech preserved
    assert any(u.forced for u in utts)


def test_segmenter_drops_clicks_and_flushes_midspeech():
    x = timeline([("silence", 0.5), ("speech", 0.1), ("silence", 1.0), ("speech", 1.0)])
    seg = Segmenter("mixed", EnergyVAD())
    utts = seg.push(x) + seg.flush()  # stream ends while still speaking
    assert len(utts) == 1  # the 100 ms click was ignored, trailing speech flushed
    assert utts[0].audio.size / 16000 > 0.8


def test_segmenter_results_do_not_depend_on_chunk_sizes():
    x = timeline([("silence", 0.5), ("speech", 2.0), ("silence", 1.0), ("speech", 1.2), ("silence", 1.0)])
    a = Segmenter("mixed", EnergyVAD())
    ua = a.push(x) + a.flush()
    b = Segmenter("mixed", EnergyVAD())
    ub = []
    rng = np.random.default_rng(3)
    i = 0
    while i < len(x):
        n = int(rng.integers(1, 2000))
        ub += b.push(x[i : i + n])
        i += n
    ub += b.flush()
    assert len(ua) == len(ub) == 2
    for p, q in zip(ua, ub):
        assert p.audio.size == q.audio.size
