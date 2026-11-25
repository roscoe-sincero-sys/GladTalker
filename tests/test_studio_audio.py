import unittest

from src.studio.audio import (
    FIT_SAFETY_MS,
    MAX_TEMPO,
    DialogueTooLongError,
    build_pitch_tempo_filter,
    compute_fit_tempo,
    plan_dialogue,
)
from src.studio.captions import to_srt, to_vtt


class PitchTempoFilterTests(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(build_pitch_tempo_filter(22050, 0, 1.0), "asetrate=22050,aresample=22050,atempo=1.00000")

    def test_pitch_up_compensates_speed(self):
        af = build_pitch_tempo_filter(22050, 12, 1.0)  # one octave up doubles the rate
        self.assertEqual(af, "asetrate=44100,aresample=22050,atempo=0.50000")

    def test_large_factors_are_chained(self):
        af = build_pitch_tempo_filter(16000, -12, 2.0)  # rate halves -> atempo 4.0 = 2.0 * 2.0
        self.assertEqual(af, "asetrate=8000,aresample=16000,atempo=2.0,atempo=2.00000")
        af = build_pitch_tempo_filter(16000, 12, 0.5)  # atempo 0.25 = 0.5 * 0.5
        self.assertEqual(af, "asetrate=32000,aresample=16000,atempo=0.5,atempo=0.50000")

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            build_pitch_tempo_filter(0, 0, 1.0)
        with self.assertRaises(ValueError):
            build_pitch_tempo_filter(16000, 0, 0)


class FitTempoTests(unittest.TestCase):
    def test_never_slows_down(self):
        self.assertEqual(compute_fit_tempo([1000, 1000], 42000), 1.0)

    def test_speeds_up_to_fit(self):
        tempo = compute_fit_tempo([20000, 25000], 42000)
        self.assertAlmostEqual(tempo, (45000 + 120) / (42000 - FIT_SAFETY_MS), places=6)

    def test_too_long_raises_with_minimum(self):
        with self.assertRaises(DialogueTooLongError) as cm:
            compute_fit_tempo([30000, 30000], 20000)
        self.assertGreater(cm.exception.minimum_s, 60.0 / MAX_TEMPO)
        self.assertIn("at least", str(cm.exception))


class PlanDialogueTests(unittest.TestCase):
    SEGMENTS = [("Brain", "Click it.", 1000), ("Pinky", "I'm clicking.", 1500), ("Brain", "Harder.", 800)]

    def _check_owner_consistency(self, plan):
        counts = {}
        for owner in plan.frame_owner:
            if owner is None:
                continue
            name, idx = owner
            self.assertEqual(idx, counts.get(name, 0), "per-speaker frame indices must be contiguous")
            counts[name] = idx + 1
        self.assertEqual(counts, plan.speaker_frames)

    def test_target_duration(self):
        plan = plan_dialogue(self.SEGMENTS, target_ms=6000, fps=25)
        self.assertEqual(plan.total_ms, 6000)
        self.assertEqual(plan.total_frames, 150)
        self.assertEqual(len(plan.frame_owner), 150)
        self.assertEqual(plan.gap_ms, (6000 - 3300) // 4)
        self.assertEqual(plan.spans[0].start_ms, plan.gap_ms)
        self.assertEqual(plan.speaker_frames, {"Brain": 25 + 20, "Pinky": 37})  # Pinky: 2350-3850 ms -> frames 59..96
        self._check_owner_consistency(plan)

    def test_natural_pace(self):
        plan = plan_dialogue(self.SEGMENTS, target_ms=None, gap_ms=300)
        self.assertEqual(plan.total_ms, 3300 + 4 * 300)
        self.assertEqual(plan.spans[1].start_ms, 300 + 1000 + 300)
        self._check_owner_consistency(plan)

    def test_overlong_dialogue_is_clipped_to_target(self):
        plan = plan_dialogue(self.SEGMENTS, target_ms=2000)
        self.assertEqual(plan.gap_ms, 0)
        self.assertEqual(plan.total_frames, 50)
        self.assertEqual(plan.spans[-1].frames, 0)
        self._check_owner_consistency(plan)


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.spans = plan_dialogue([("Brain", "Click\n it.", 1000), ("Pinky <3", "A & B", 61_500)],
                                   target_ms=None, gap_ms=250).spans

    def test_srt(self):
        srt = to_srt(self.spans)
        self.assertTrue(srt.startswith("1\n00:00:00,250 --> 00:00:01,250\nBrain: Click it.\n"))
        self.assertIn("2\n00:00:01,500 --> 00:01:03,000\nPinky <3: A & B\n", srt)

    def test_vtt_escapes_and_voice_tags(self):
        vtt = to_vtt(self.spans)
        self.assertTrue(vtt.startswith("WEBVTT\n\n00:00:00.250 --> 00:00:01.250\n<v Brain>Brain: Click it.\n"))
        self.assertIn("<v Pinky &lt;3>Pinky &lt;3: A &amp; B", vtt)


if __name__ == "__main__":
    unittest.main()
