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

    def test_long_cast_names_are_recognised(self):
        cast = ["Alice", "The Man in the Hat"]
        r = parse_script("Alice: Who are you?\nThe Man in the Hat: Nobody.\n"
                         "- **the  man in the hat** (quietly): Shh.", cast=cast)
        self.assertEqual([(l.speaker, l.text) for l in r.lines], [
            ("Alice", "Who are you?"), ("The Man in the Hat", "Nobody."), ("The Man in the Hat", "Shh."),
        ])
        self.assertEqual(r.lines[2].note, "quietly")
        self.assertEqual(r.unknown_speakers, [])

    def test_longest_cast_name_wins(self):
        r = parse_script("Al Bundy: Hi.\nAl: Hey.", cast=["Al", "Al Bundy"])
        self.assertEqual([l.speaker for l in r.lines], ["Al Bundy", "Al"])

    def test_cast_label_spelling_is_preserved(self):
        """A label with unusual internal spacing is still that cast member, spelled as stored,
        and the imported lines render without manual correction."""
        from src.studio.schemas import RenderRequest

        cast = ["Officer  Mia", "Bob"]
        parsed = parse_script("Officer Mia: Freeze.\nOFFICER   MIA: Hands up.\nOFFICER  MIA\nStay there.",
                              cast=cast)
        self.assertEqual([l.speaker for l in parsed.lines], ["Officer  Mia"] * 3)
        self.assertEqual(parsed.unknown_speakers, [])
        req = RenderRequest.model_validate({
            "image_path": "scene.png",
            "cast": [{"name": n, "region": {"x": 0, "y": 0, "w": 64, "h": 64}} for n in cast],
            "script": [{"speaker": l.speaker, "text": l.text} for l in parsed.lines],
        })
        self.assertEqual({l.speaker for l in req.script}, {"Officer  Mia"})

    def test_cast_label_with_literal_underscores_is_preserved(self):
        """Underscores that belong to a cast name are not Markdown emphasis: the imported lines keep
        the stored spelling and render with that cast, while emphasis around a name still works."""
        from src.studio.schemas import RenderRequest

        cast = ["Crew_", "_Pilot", "Brain"]
        parsed = parse_script("Crew_: Ready.\n**Crew_**: Set.\n_Pilot: Go.\n__Brain__: Click it.\n"
                              "CREW_\nStill here.\n**_Pilot**\nLanding.", cast=cast)
        self.assertEqual([(l.speaker, l.text) for l in parsed.lines], [
            ("Crew_", "Ready."), ("Crew_", "Set."), ("_Pilot", "Go."), ("Brain", "Click it."),
            ("Crew_", "Still here."), ("_Pilot", "Landing."),
        ])
        self.assertEqual(parsed.unknown_speakers, [])
        req = RenderRequest.model_validate({
            "image_path": "scene.png",
            "cast": [{"name": n, "region": {"x": 0, "y": 0, "w": 64, "h": 64}} for n in cast],
            "script": [{"speaker": l.speaker, "text": l.text} for l in parsed.lines],
        })
        self.assertEqual([l.speaker for l in req.script], [l.speaker for l in parsed.lines])

    def _render_speakers(self, cast, parsed):
        from src.studio.schemas import RenderRequest

        req = RenderRequest.model_validate({
            "image_path": "scene.png",
            "cast": [{"name": n, "region": {"x": 0, "y": 0, "w": 64, "h": 64}} for n in cast],
            "script": [{"speaker": l.speaker, "text": l.text} for l in parsed.lines],
        })
        return [(l.speaker, l.text) for l in req.script]

    def test_screenplay_headers_use_unicode_case_folding(self):
        """"STRASSE" is "Straße" (as render matching treats it), so the header starts a new line for
        Straße instead of being folded into Brain's line."""
        cast = ["Brain", "Straße"]
        parsed = parse_script("BRAIN\nHi.\n\nSTRASSE\nGuten Tag.\n\nSTRASSE: Tschüss.", cast=cast)
        expected = [("Brain", "Hi."), ("Straße", "Guten Tag."), ("Straße", "Tschüss.")]
        self.assertEqual([(l.speaker, l.text) for l in parsed.lines], expected)
        self.assertEqual(parsed.unknown_speakers, [])
        self.assertEqual(self._render_speakers(cast, parsed), expected)

    def test_overlapping_underscore_names_keep_their_own_speaker(self):
        cast = ["Crew", "_Crew"]
        parsed = parse_script("_Crew: One.\nCrew: Two.\n__Crew__: Three.\n_CREW\nFour.\n\nCREW\nFive.\n"
                              "**_Crew**\nSix.", cast=cast)
        expected = [("_Crew", "One."), ("Crew", "Two."), ("Crew", "Three."), ("_Crew", "Four."),
                    ("Crew", "Five."), ("_Crew", "Six.")]
        self.assertEqual([(l.speaker, l.text) for l in parsed.lines], expected)
        self.assertEqual(parsed.unknown_speakers, [])
        self.assertEqual(self._render_speakers(cast, parsed), expected)

    def test_balanced_emphasis_means_the_same_speaker_inline_and_as_a_header(self):
        """The whole label is compared first ("_Crew" is _Crew), then one matched pair of emphasis
        markers is removed ("_Crew_" is emphasised Crew), the same way on both kinds of line."""
        cast = ["Crew", "_Crew"]
        parsed = parse_script("_Crew_: Hello.\n_Crew: Hi.\n**_Crew_**: Hey.\n**Crew:** Yo.\n\n"
                              "_Crew_\nFrom the header.\n\n_Crew\nFrom the other header.", cast=cast)
        expected = [("Crew", "Hello."), ("_Crew", "Hi."),
                    ("Crew", "Hey."),  # bold around italic Crew
                    ("Crew", "Yo."), ("Crew", "From the header."), ("_Crew", "From the other header.")]
        self.assertEqual([(l.speaker, l.text) for l in parsed.lines], expected)
        self.assertEqual(parsed.unknown_speakers, [])
        self.assertEqual(self._render_speakers(cast, parsed), expected)

    def test_cast_names_with_parentheses_or_colons_beat_their_shorter_prefix(self):
        """A complete cast label wins over a shorter cast name before "(" or ":" is read as a note
        or separator; a parenthetical that isn't part of a name is still the note."""
        cast = ["Dr. Moss", "Dr. Moss (Guest)", "Team", "Team: Blue"]
        parsed = parse_script("Dr. Moss (Guest): Ready.\nDr. Moss: Hi.\nDr. Moss (nervous): Um.\n"
                              "Team: Blue: Ready.\nTeam: Go.\n**Team: Blue**: Bold.\nTeam: Blue\nWe win.\n\n"
                              "DR. MOSS (GUEST)\nAs a header.", cast=cast)
        expected = [("Dr. Moss (Guest)", "Ready."), ("Dr. Moss", "Hi."), ("Dr. Moss", "Um."),
                    ("Team: Blue", "Ready."), ("Team", "Go."), ("Team: Blue", "Bold."),
                    ("Team: Blue", "We win."), ("Dr. Moss (Guest)", "As a header.")]
        self.assertEqual([(l.speaker, l.text) for l in parsed.lines], expected)
        self.assertEqual([l.note for l in parsed.lines], [None, None, "nervous", None, None, None, None, None])
        self.assertEqual(parsed.unknown_speakers, [])
        self.assertEqual(self._render_speakers(cast, parsed), expected)

    def test_a_cast_name_that_looks_like_a_list_marker_is_tried_as_typed_first(self):
        cast = ["Operator", "- Operator", "Brain"]
        parsed = parse_script("- Operator\nCome in.\n\n- Operator: Over.\n- **Brain:** Bulleted.\n"
                              "* Operator: Also bulleted.\nOperator: Plain.", cast=cast)
        expected = [("- Operator", "Come in."), ("- Operator", "Over."), ("Brain", "Bulleted."),
                    ("Operator", "Also bulleted."), ("Operator", "Plain.")]
        self.assertEqual([(l.speaker, l.text) for l in parsed.lines], expected)
        self.assertEqual(self._render_speakers(cast, parsed), expected)

    def test_finding_a_label_never_depends_on_how_long_the_dialogue_is(self):
        """Colons in the dialogue (clock times) aren't examined beyond the longest possible label,
        and the dialogue is sliced once, after the label is chosen."""
        from unittest import mock

        from src.studio import script

        cast = ["Dr. Moss", "Dr. Moss (Guest)"]
        times = ", ".join(f"{h}:{m:02d}" for h in range(1, 13) for m in (0, 15, 30, 45))
        processed = []
        real_key = script.name_key

        def counting_key(name):
            processed.append(len(name))
            return real_key(name)

        work = {}
        for repeat in (1, 40):
            processed.clear()
            line = f"Dr. Moss (Guest): Meet me at {times}." * repeat
            with mock.patch.object(script, "name_key", counting_key):
                parsed = parse_script(line, cast=cast)
            self.assertEqual(parsed.lines[0].speaker, "Dr. Moss (Guest)")
            self.assertTrue(parsed.lines[0].text.startswith("Meet me at 1:00"))
            work[len(line)] = sum(processed)
        short, long = work.values()
        self.assertEqual(long, short, work)  # a 40x longer line costs nothing more to label
        self.assertLess(short, 3_000, work)  # about 20 colons within reach of a 16-character name

    def test_padding_never_pushes_a_longer_cast_name_out_of_reach(self):
        """The search limit ignores spacing, like name matching: alignment spaces or a note before
        the colon still find "Team: Blue" rather than the shorter "Team"."""
        cast = ["Team", "Team: Blue"]
        pad = " " * 300
        parsed = parse_script(f"Team: Blue (calmly){pad}: Ready.\nTeam:{pad}Blue{pad}: Go.\n"
                              f"Team{pad}: Plain.\nTEAM:  BLUE\nWe win.", cast=cast)
        expected = [("Team: Blue", "Ready."), ("Team: Blue", "Go."), ("Team", "Plain."), ("Team: Blue", "We win.")]
        self.assertEqual([(l.speaker, l.text) for l in parsed.lines], expected)
        self.assertEqual(parsed.lines[0].note, "calmly")
        self.assertEqual(self._render_speakers(cast, parsed), expected)

    def test_long_cast_names_that_dont_fit_cost_nothing_per_line(self):
        """The label search follows only cast names that still match the text, so a long cast entry
        (unrelated, or sharing the speaker's first words) adds no work to each line."""
        from unittest import mock

        from src.studio import script

        times = ", ".join(f"{h}:{m:02d}" for h in range(1, 13) for m in (0, 15, 30, 45))
        line = f"Dr. Moss (Guest): Meet me at {times}."
        real_key = script.name_key

        def per_line_work(cast):
            totals = []
            for count in (1, 20):
                processed = []
                with mock.patch.object(script, "name_key", lambda name: (processed.append(len(name)), real_key(name))[1]):
                    parsed = parse_script("\n".join([line] * count), cast=cast)
                self.assertEqual({l.speaker for l in parsed.lines}, {"Dr. Moss (Guest)"})
                totals.append(sum(processed))
            return (totals[1] - totals[0]) / 19

        short = ["Dr. Moss", "Dr. Moss (Guest)"]
        baseline = per_line_work(short)
        self.assertLess(baseline, 3_000)
        for extra in ("Narrator" * 1_500, "Dr. Moss " + "the Elder " * 1_000):
            self.assertEqual(per_line_work(short + [extra]), baseline, len(extra))

    def test_a_huge_run_of_spaces_is_read_once(self):
        from unittest import mock

        from src.studio import script

        processed = []
        real_key = script.name_key

        def counting_key(name):
            processed.append(len(name))
            return real_key(name)

        line = "Team: Blue" + " " * 100_000 + ": Ready. " + "Then at 10:15: go. " * 200
        with mock.patch.object(script, "name_key", counting_key):
            parsed = parse_script(line, cast=["Team", "Team: Blue"])
        self.assertEqual((parsed.lines[0].speaker, parsed.lines[0].text[:6]), ("Team: Blue", "Ready."))
        self.assertLess(sum(processed), 3_000)

    def test_a_headers_note_goes_with_the_line_after_it(self):
        parsed = parse_script("Mia (quietly):\nShh.\n\nOut loud now.\n\n**Mia** (whispering)\nPsst.", cast=["Mia"])
        self.assertEqual([(l.speaker, l.note, l.text) for l in parsed.lines], [
            ("Mia", "quietly", "Shh."), ("Mia", None, "Out loud now."), ("Mia", "whispering", "Psst."),
        ])

    def test_emphasis_is_still_stripped_from_names_outside_the_cast(self):
        r = parse_script("_Narrator_: Meanwhile.\n**Brain**: Hi.", cast=["Brain"])
        self.assertEqual([l.speaker for l in r.lines], ["Narrator", "Brain"])
        self.assertEqual(r.unknown_speakers, ["Narrator"])

    def test_render_matches_speakers_ignoring_case_and_spacing(self):
        from src.studio.schemas import RenderRequest

        req = RenderRequest.model_validate({
            "image_path": "scene.png",
            "cast": [{"name": "Officer  Mia", "region": {"x": 0, "y": 0, "w": 64, "h": 64}}],
            "script": [{"speaker": "officer mia", "text": "Hi."}],
        })
        self.assertEqual(req.script[0].speaker, "Officer  Mia")
        with self.assertRaisesRegex(ValueError, "more than once"):
            RenderRequest.model_validate({
                "image_path": "scene.png",
                "cast": [{"name": n, "region": {"x": 0, "y": 0, "w": 64, "h": 64}} for n in ("A  B", "a b")],
                "script": [{"speaker": "A B", "text": "Hi."}],
            })

    def test_parsed_script_is_accepted_by_render(self):
        """Contract: whatever /script/parse returns for a valid cast must validate in /render/start
        with each line attributed to the right cast member."""
        from src.studio.schemas import MAX_CAST, RenderRequest

        names = ["Alice", "The Man in the Hat", "Dr. Jean-Luc O’Brien the Third of Many"]
        self.assertLessEqual(len(names), MAX_CAST)
        text = "\n".join(f"{n.upper()}: Line {i}." for i, n in enumerate(names))
        parsed = parse_script(text, cast=names)
        self.assertEqual(parsed.unknown_speakers, [])
        req = RenderRequest.model_validate({
            "image_path": "scene.png",
            "cast": [{"name": n, "region": {"x": 0, "y": 0, "w": 64, "h": 64}} for n in names],
            "script": [{"speaker": l.speaker, "text": l.text} for l in parsed.lines],
        })
        self.assertEqual([(l.speaker, l.text) for l in req.script],
                         [(n, f"Line {i}.") for i, n in enumerate(names)])

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
