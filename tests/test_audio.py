import base64
import json
import struct
import tempfile
import unittest
from pathlib import Path

from rayplayer import audio as au
from rayplayer import render as rd
from rayplayer.panel import Panel, Speaker, attach_voices
from rayplayer.voices import VoiceError, build, parse_gemini_audio


def seat(name, is_host=False):
    return Speaker(name=name, provider_spec={"kind": "mock", "model": "m"}, is_host=is_host, voice_spec={"name": name})


def run_record(*speakers):
    return {
        "show": "Show",
        "topic": "topic",
        "turns": [
            {"index": i, "speaker": s, "role": "panelist", "text": f"line {i} from {s}", "model": "m", "provider": "mock"}
            for i, s in enumerate(speakers)
        ],
    }


class TestWav(unittest.TestCase):
    def test_roundtrip(self):
        pcm = b"".join(struct.pack("<h", i % 1000) for i in range(2400))
        with tempfile.TemporaryDirectory() as d:
            p = au.write_wav(Path(d) / "a.wav", pcm, 24000)
            back, rate = au.read_wav(p)
        self.assertEqual(back, pcm)
        self.assertEqual(rate, 24000)

    def test_silence_length(self):
        self.assertEqual(len(au.silence(0.5, 24000)), 24000)  # 12000 frames x 2 bytes

    def test_stitch_inserts_one_gap_between_clips(self):
        clip = b"\x01\x02" * 24000  # 1 second
        pcm, rate = au.stitch([(clip, 24000), (clip, 24000)], gap_seconds=0.5)
        self.assertEqual(rate, 24000)
        self.assertAlmostEqual(len(pcm) / 2 / rate, 2.5, places=3)

    def test_stitch_refuses_mixed_sample_rates(self):
        with self.assertRaises(ValueError) as cm:
            au.stitch([(b"\x00\x00", 24000), (b"\x00\x00", 22050)], 0.4)
        self.assertIn("sample rate", str(cm.exception))

    def test_timestamp(self):
        self.assertEqual(au.timestamp(0), "00:00")
        self.assertEqual(au.timestamp(75.9), "01:15")


class TestMockVoice(unittest.TestCase):
    def test_length_scales_with_text(self):
        v = build({"name": "Kore"}, offline=True)
        self.assertGreater(v.say("a much longer line with many more words in it than the other").seconds,
                           v.say("short").seconds)

    def test_seats_get_distinct_tones(self):
        a = build({"name": "Kore"}, offline=True).say("同樣的一句話")
        b = build({"name": "Puck"}, offline=True).say("同樣的一句話")
        self.assertEqual(len(a.pcm), len(b.pcm))
        self.assertNotEqual(a.pcm, b.pcm)

    def test_missing_key_is_a_clear_error(self):
        v = build({"kind": "gemini", "model": "x", "name": "Kore", "api_key_env": "DEFINITELY_NOT_SET_9137"})
        with self.assertRaises(VoiceError) as cm:
            v.say("hello")
        self.assertIn("DEFINITELY_NOT_SET_9137", str(cm.exception))

    def test_style_is_prepended_as_a_direction(self):
        v = build({"name": "Kore", "style": "以懷疑的語氣說"}, offline=True)
        self.assertEqual(v.styled("這樣講不對"), "以懷疑的語氣說: 這樣講不對")


class TestGeminiParsing(unittest.TestCase):
    def _resp(self, mime, pcm=b"\x01\x02\x03\x04"):
        return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime, "data": base64.b64encode(pcm).decode()}}]}}]}

    def test_reads_rate_out_of_the_mimetype(self):
        a = parse_gemini_audio(self._resp("audio/L16;codec=pcm;rate=24000"))
        self.assertEqual(a.sample_rate, 24000)
        self.assertEqual(a.pcm, b"\x01\x02\x03\x04")

    def test_missing_rate_is_an_error(self):
        with self.assertRaises(VoiceError):
            parse_gemini_audio(self._resp("audio/L16;codec=pcm"))

    def test_blocked_response_is_an_error(self):
        with self.assertRaises(VoiceError):
            parse_gemini_audio({"candidates": [{"finishReason": "SAFETY"}]})


class TestRender(unittest.TestCase):
    def _panel(self):
        p = Panel("Show", "zh-Hant", [seat("A"), seat("B")], seat("H", True))
        return attach_voices(p, offline=True)

    def test_renders_an_episode_and_a_cue_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            out = rd.render(run_record("H", "A", "B"), self._panel(), d, gap_seconds=0.3)
            self.assertEqual(out["clips"], 3)
            self.assertTrue(out["episode"].exists())
            self.assertEqual(len(list(Path(d).glob("turn-*.wav"))), 3)
            cues = out["cues"].read_text()
            self.assertIn("00:00  H", cues)
            self.assertEqual(len(cues.strip().splitlines()), 5)  # title, blank, 3 cues

    def test_second_render_reuses_existing_clips(self):
        with tempfile.TemporaryDirectory() as d:
            rec, pnl = run_record("A", "B"), self._panel()
            rd.render(rec, pnl, d)
            seen = []
            rd.render(rec, pnl, d, on_clip=lambda i, s, p, reused: seen.append(reused))
            self.assertEqual(seen, [True, True])

    def test_speaker_missing_from_the_panel_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as cm:
                rd.render(run_record("A", "Nobody"), self._panel(), d)
            self.assertIn("Nobody", str(cm.exception))

    def test_episode_length_matches_the_cue_sheet_timeline(self):
        with tempfile.TemporaryDirectory() as d:
            out = rd.render(run_record("A", "B", "A"), self._panel(), d, gap_seconds=0.4)
            pcm, rate = au.read_wav(out["episode"])
            self.assertAlmostEqual(len(pcm) / 2 / rate, out["seconds"], places=3)


if __name__ == "__main__":
    unittest.main()
