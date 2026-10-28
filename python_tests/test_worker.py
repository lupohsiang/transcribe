import unittest
from types import SimpleNamespace

from python.worker import split_intervals


def segment(start: float, end: float, speaker: str) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, speaker=speaker)


class SplitIntervalsTests(unittest.TestCase):
    def test_partitions_overlapping_speakers_into_non_overlapping_unions(self) -> None:
        intervals, has_overlap, has_split = split_intervals(
            [segment(0, 3, "speaker_0"), segment(1, 2, "speaker_1")], 4
        )

        self.assertEqual(
            intervals,
            [
                (0, 1, ("speaker_0",)),
                (1, 2, ("speaker_0", "speaker_1")),
                (2, 3, ("speaker_0",)),
            ],
        )
        self.assertTrue(has_overlap)
        self.assertFalse(has_split)

    def test_merges_shared_speaker_boundaries_and_preserves_gaps(self) -> None:
        intervals, has_overlap, has_split = split_intervals(
            [segment(0, 1, "speaker_0"), segment(1, 2, "speaker_0"), segment(3, 4, "speaker_0")], 5
        )

        self.assertEqual(intervals, [(0, 2, ("speaker_0",)), (3, 4, ("speaker_0",))])
        self.assertFalse(has_overlap)
        self.assertFalse(has_split)

    def test_tracks_multiple_spans_with_the_same_speaker_as_one_active_set(self) -> None:
        intervals, has_overlap, _ = split_intervals(
            [segment(0, 2, "speaker_0"), segment(1, 3, "speaker_0")], 4
        )

        self.assertEqual(intervals, [(0, 3, ("speaker_0",))])
        self.assertFalse(has_overlap)

    def test_splits_long_merged_interval_at_25_second_asr_boundaries(self) -> None:
        intervals, has_overlap, has_split = split_intervals([segment(0, 55, "speaker_0")], 60)

        self.assertEqual(
            intervals,
            [(0, 25.0, ("speaker_0",)), (25.0, 50.0, ("speaker_0",)), (50.0, 55, ("speaker_0",))],
        )
        self.assertFalse(has_overlap)
        self.assertTrue(has_split)

    def test_rejects_malformed_and_out_of_range_segments(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid time bounds"):
            split_intervals([segment(2, 1, "speaker_0")], 4)
        with self.assertRaisesRegex(ValueError, "outside the selected audio"):
            split_intervals([segment(0, 5, "speaker_0")], 4)
        with self.assertRaisesRegex(ValueError, "without a speaker"):
            split_intervals([SimpleNamespace(start=0, end=1, speaker=None)], 4)


if __name__ == "__main__":
    unittest.main()
