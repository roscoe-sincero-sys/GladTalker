import unittest

from src.studio.script import lint_line, parse_script


class ParseScriptTests(unittest.TestCase):
    def test_basic_speaker_lines(self):
        r = parse_script("Brain: I'll show you how.\nPinky: I'm clicking.\n")
        self.assertEqual([(l.speaker, l.text) for l in r.lines],
                         [("Brain", "I'll show you how."), ("Pinky", "I'm clicking.")])
        self.assertEqual(r.speakers, ["Brain", "Pinky"])
        self.assertEqual(r.unknown_speakers, [])

    def test_markdown_bullets_and_parentheticals(self):
        text = "- **Brain:** Click harder.\n* __Pinky__: Okay.\nBrain (sighing): Start over.\n> Pinky: Fine."
        r = parse_script(text)
        self.assertEqual([(l.speaker, l.text) for l in r.lines], [
            ("Brain", "Click harder."), ("Pinky", "Okay."), ("Brain", "Start over."), ("Pinky", "Fine."),
        ])
        self.assertEqual(r.lines[2].note, "sighing")

    def test_continuation_lines_join_previous(self):
        r = parse_script("Brain: Click the other button.\nI know it works.\n\nPinky: Its greyed out too.")
        self.assertEqual(r.lines[0].text, "Click the other button. I know it works.")
        self.assertEqual(len(r.lines), 2)

    def test_case_insensitive_cast_matching(self):
        r = parse_script("BRAIN: Hi.\nbrain: Again.\npinky: Yes.", cast=["Brain", "Pinky"])
        self.assertEqual([l.speaker for l in r.lines], ["Brain", "Brain", "Pinky"])

    def test_first_spelling_wins_without_cast(self):
        r = parse_script("Brain: Hi.\nBRAIN: Again.")
        self.assertEqual([l.speaker for l in r.lines], ["Brain", "Brain"])

    def test_screenplay_style_with_cast(self):
        r = parse_script("BRAIN\nClick it.\n\nPINKY\nI'm clicking.", cast=["Brain", "Pinky"])
        self.assertEqual([(l.speaker, l.text) for l in r.lines],
                         [("Brain", "Click it."), ("Pinky", "I'm clicking.")])

    def test_unknown_speaker_flagged(self):
        r = parse_script("Brain: Hi.\nNarrator: Meanwhile.", cast=["Brain", "Pinky"])
        self.assertEqual(r.unknown_speakers, ["Narrator"])
        self.assertEqual(r.lines[1].warnings[0].code, "unknown_speaker")

    def test_line_without_speaker_flagged(self):
        r = parse_script("Just some words.")
        self.assertIsNone(r.lines[0].speaker)
        self.assertEqual(r.lines[0].warnings[0].code, "no_speaker")

    def test_urls_and_times_are_not_speakers(self):
        r = parse_script("Brain: See https://example.com now.\nhttps://example.com/path")
        self.assertEqual(len(r.lines), 1)
        r = parse_script("11:17 AM is the time.")
        self.assertIsNone(r.lines[0].speaker)

    def test_to_dict_shape(self):
        d = parse_script("Brain: You're clicking it wrong.").to_dict()
        self.assertEqual(d["line_count"], 1)
        self.assertEqual(d["warning_count"], 1)
        self.assertEqual(d["lines"][0]["warnings"][0]["code"], "tts_contraction")


class LintTests(unittest.TestCase):
    def test_contraction_autofix(self):
        (w,) = lint_line("You're doing it wrong and they’re late.")
        self.assertEqual(w.code, "tts_contraction")
        self.assertEqual(w.fix, "your doing it wrong and their late.")

    def test_long_line(self):
        codes = [w.code for w in lint_line("word " * 80)]
        self.assertIn("long_line", codes)

    def test_no_speech(self):
        self.assertEqual([w.code for w in lint_line("...!?")], ["no_speech"])

    def test_clean_line(self):
        self.assertEqual(lint_line("Click better."), [])


if __name__ == "__main__":
    unittest.main()
