import unittest

from rayplayer import orchestrator as orc
from rayplayer.panel import Panel, Speaker
from rayplayer.providers import Msg, merge_turns

NAMES = ["Ray", "Claude", "GPT", "Gemini", "Grok"]


def speaker(name, is_host=False):
    s = Speaker(name=name, provider_spec={"kind": "mock", "model": f"mock-{name}"}, is_host=is_host)
    return s


class TestClean(unittest.TestCase):
    def test_strips_own_name_prefix(self):
        self.assertEqual(orc.clean("Claude: I disagree.", NAMES), "I disagree.")
        self.assertEqual(orc.clean("**GPT**: Sure.", NAMES), "Sure.")
        self.assertEqual(orc.clean("Gemini：這樣說不對。", NAMES), "這樣說不對。")

    def test_strips_wrapping_quotes_and_markdown(self):
        self.assertEqual(orc.clean('"Just talking."', NAMES), "Just talking.")
        self.assertEqual(orc.clean("- point one\n- point two", NAMES), "point one\npoint two")
        self.assertEqual(orc.clean("## Heading\ntext", NAMES), "Heading\ntext")

    def test_keeps_a_colon_that_is_not_a_name_tag(self):
        self.assertEqual(orc.clean("My point: it is cheaper.", NAMES), "My point: it is cheaper.")


class TestLength(unittest.TestCase):
    def test_cjk_counts_without_spaces(self):
        self.assertGreater(orc.approx_words("這是一段沒有空格的中文句子。"), 5)
        self.assertEqual(orc.approx_words("three plain words"), 3)

    def test_soft_trim_cuts_at_sentence_end(self):
        text = "One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten."
        out = orc.soft_trim(text, max_words=2)
        self.assertTrue(out.endswith("."))
        self.assertLess(len(out), len(text))

    def test_short_turns_are_untouched(self):
        text = "A tight answer that fits."
        self.assertEqual(orc.soft_trim(text, max_words=90), text)


class TestAgreement(unittest.TestCase):
    def _turns(self, *texts):
        return [orc.Turn(i, "X", "panelist", t, "m", "mock") for i, t in enumerate(texts)]

    def test_detects_a_streak(self):
        turns = self._turns("I agree, and more.", "Exactly right.", "沒錯，我也這樣想。")
        self.assertEqual(orc.agreement_streak(turns), 3)

    def test_disagreement_breaks_the_streak(self):
        turns = self._turns("I agree.", "No, that is wrong.")
        self.assertEqual(orc.agreement_streak(turns), 0)

    def test_host_turn_ends_the_streak(self):
        turns = self._turns("I agree.", "Agreed.")
        turns.append(orc.Turn(2, "Ray", "host", "Next question.", "m", "mock"))
        self.assertEqual(orc.agreement_streak(turns), 0)


class TestHistory(unittest.TestCase):
    def test_own_lines_come_back_as_assistant(self):
        me = speaker("Claude")
        turns = [
            orc.Turn(0, "Ray", "host", "Opening.", "m", "mock"),
            orc.Turn(1, "Claude", "panelist", "My take.", "m", "mock"),
            orc.Turn(2, "GPT", "panelist", "Counterpoint.", "m", "mock"),
        ]
        msgs = orc._history_for(me, turns)
        self.assertEqual([m.role for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[1].content, "My take.")
        self.assertTrue(msgs[2].content.startswith("GPT: "))

    def test_merge_turns_alternates_and_opens_on_user(self):
        merged = merge_turns([Msg("assistant", "a"), Msg("user", "b"), Msg("user", "c")])
        self.assertEqual([m.role for m in merged], ["user", "assistant", "user"])
        self.assertEqual(merged[2].content, "b\n\nc")


class TestRotation(unittest.TestCase):
    def _panel(self):
        return Panel("Show", "en", [speaker(n) for n in ["Claude", "GPT", "Gemini", "Grok"]], speaker("Ray", True))

    def test_nobody_speaks_twice_in_a_row(self):
        p = self._panel()
        turns = []
        for i in range(12):
            s = orc.next_speaker(p, turns, "topic")
            if turns:
                self.assertNotEqual(s.name, turns[-1].speaker)
            turns.append(orc.Turn(i, s.name, "panelist", "x", "m", "mock"))

    def test_turns_are_evenly_distributed(self):
        p = self._panel()
        turns = []
        for i in range(12):
            s = orc.next_speaker(p, turns, "topic")
            turns.append(orc.Turn(i, s.name, "panelist", "x", "m", "mock"))
        counts = {n: sum(1 for t in turns if t.speaker == n) for n in ["Claude", "GPT", "Gemini", "Grok"]}
        self.assertEqual(set(counts.values()), {3})


class TestRecording(unittest.TestCase):
    def test_offline_episode_has_host_bookends_and_requested_turns(self):
        p = Panel("Show", "en", [speaker(n) for n in ["A", "B", "C"]], speaker("H", True))
        for s in p.everyone:
            from rayplayer.providers import build
            s.provider = build(s.provider_spec, offline=True)
        run = orc.record(p, "topic", turns=6, host_every=3)
        self.assertEqual(run.errors, [])
        self.assertEqual(sum(1 for t in run.turns if t.role == "panelist"), 6)
        self.assertEqual(run.turns[0].speaker, "H")
        self.assertEqual(run.turns[-1].speaker, "H")

    def test_dissent_nudge_fires_when_the_room_agrees(self):
        p = Panel("Show", "en", [speaker(n) for n in ["A", "B"]], None)
        for s in p.everyone:
            from rayplayer.providers import build
            s.provider = build(s.provider_spec, offline=True)
        agreeing = [orc.Turn(i, "A", "panelist", "I agree.", "m", "mock") for i in range(3)]
        self.assertGreaterEqual(orc.agreement_streak(agreeing), 3)
        turn = orc._speak(p.speakers[1], p, "topic", agreeing, orc.prompts.DISSENT_CUE, 3)
        self.assertTrue(turn.nudged)


if __name__ == "__main__":
    unittest.main()
