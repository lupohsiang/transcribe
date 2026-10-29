import unittest
from unittest.mock import patch
from types import SimpleNamespace

from python.worker import (
    Activity,
    Recognition,
    aligned_units,
    alignment_language,
    assemble_window,
    recognition_windows,
    speaker_timeline,
    supported_alignment_languages,
)


def segment(start: float, end: float, speaker: str) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, speaker=speaker)


def item(text: str, start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(text=text, start_time=start, end_time=end)


class SpeakerTimelineTests(unittest.TestCase):
    def test_partitions_overlap_and_gaps_into_non_overlapping_activity(self) -> None:
        timeline = speaker_timeline([segment(0, 3, 'speaker_0'), segment(1, 2, 'speaker_1')], 4)
        self.assertEqual(timeline, (
            Activity(0, 1, ('speaker_0',)),
            Activity(1, 2, ('speaker_0', 'speaker_1')),
            Activity(2, 3, ('speaker_0',)),
            Activity(3, 4, ()),
        ))

    def test_merges_shared_speaker_boundaries_and_preserves_gaps(self) -> None:
        timeline = speaker_timeline([
            segment(0, 1, 'speaker_0'), segment(1, 2, 'speaker_0'), segment(3, 4, 'speaker_0'),
        ], 5)
        self.assertEqual(timeline, (
            Activity(0, 2, ('speaker_0',)), Activity(2, 3, ()),
            Activity(3, 4, ('speaker_0',)), Activity(4, 5, ()),
        ))

    def test_tracks_multiple_spans_with_the_same_speaker_as_one_active_set(self) -> None:
        self.assertEqual(speaker_timeline([
            segment(0, 2, 'speaker_0'), segment(1, 3, 'speaker_0'),
        ], 4), (Activity(0, 3, ('speaker_0',)), Activity(3, 4, ())))

    def test_speaker_timeline_does_not_impose_recognition_window_limits(self) -> None:
        self.assertEqual(speaker_timeline([segment(0, 55, 'speaker_0')], 60), (
            Activity(0, 55, ('speaker_0',)), Activity(55, 60, ()),
        ))

    def test_empty_diarization_is_unknown_activity(self) -> None:
        self.assertEqual(speaker_timeline([], 4), (Activity(0, 4, ()),))

    def test_rejects_malformed_and_out_of_range_segments(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid time bounds'):
            speaker_timeline([segment(2, 1, 'speaker_0')], 4)
        with self.assertRaisesRegex(ValueError, 'outside the selected audio'):
            speaker_timeline([segment(0, 5, 'speaker_0')], 4)
        with self.assertRaisesRegex(ValueError, 'without a speaker'):
            speaker_timeline([SimpleNamespace(start=0, end=1, speaker=None)], 4)


class RecognitionWindowsTests(unittest.TestCase):
    def test_rebalances_short_tail_and_preserves_every_frame_once(self) -> None:
        windows = recognition_windows(61 * 16000 + 1)
        self.assertEqual([(w.first_frame, w.end_frame) for w in windows], [
            (0, 480000), (480000, 728000), (728000, 976001),
        ])
        self.assertEqual([(w.start, w.end) for w in recognition_windows(60 * 16000)], [
            (0.0, 30.0), (30.0, 60.0),
        ])

    def test_keeps_a_long_enough_tail_and_short_single_selection(self) -> None:
        self.assertEqual([(w.start, w.end) for w in recognition_windows(35 * 16000)], [
            (0.0, 30.0), (30.0, 35.0),
        ])
        self.assertEqual([(w.start, w.end) for w in recognition_windows(16000)], [(0.0, 1.0)])
        with self.assertRaisesRegex(ValueError, 'PCM frames'):
            recognition_windows(0)


class AlignmentTests(unittest.TestCase):
    def assemble(self, text, items, timeline=None, start=0, end=4):
        recognition = Recognition(start, end, text, 'Chinese')
        units, degenerate = aligned_units(recognition, items)
        output = assemble_window(recognition, units, timeline or (Activity(start, end, ()),))
        return output, degenerate

    def test_preserves_chinese_punctuation_spaces_and_mixed_english(self) -> None:
        output, degenerate = self.assemble(' 你好， AI! 世界。 ', [
            item('你', .1, .2), item('好', .2, .4), item('AI', 1.1, 1.4),
            item('世', 2.1, 2.3), item('界', 2.3, 2.6),
        ], (Activity(0, 1, ('A',)), Activity(1, 2, ()), Activity(2, 4, ('B',))))
        self.assertEqual(output, [
            {'start': .1, 'end': .4, 'text': ' 你好， ', 'speaker': 'A', 'activeSpeakers': ['A']},
            {'start': 1.1, 'end': 1.4, 'text': 'AI! ', 'speaker': None, 'activeSpeakers': []},
            {'start': 2.1, 'end': 2.6, 'text': '世界。 ', 'speaker': 'B', 'activeSpeakers': ['B']},
        ])
        self.assertFalse(degenerate)

    def test_preserves_english_punctuation_case_apostrophes_and_repeated_words(self) -> None:
        output, _ = self.assemble('“Don\'t stop, stop... now!” ', [
            item("Don't", .1, .5), item('stop', .6, .8), item('stop', 1.1, 1.4), item('now', 1.5, 1.8),
        ], (Activity(0, 1, ('A',)), Activity(1, 4, ('B',))))
        self.assertEqual([part['text'] for part in output], ['“Don\'t stop, ', 'stop... now!” '])
        self.assertEqual(''.join(part['text'] for part in output), '“Don\'t stop, stop... now!” ')

    def test_merges_zero_duration_items_with_adjacent_positive_items(self) -> None:
        output, degenerate = self.assemble('一，二三四五。', [
            item('一', 0, 0), item('二', .1, .3), item('三', .3, .3),
            item('四', 1.1, 1.3), item('五', 1.3, 1.3),
        ], (Activity(0, 1, ('A',)), Activity(1, 4, ('B',))))
        self.assertEqual(output, [
            {'start': .1, 'end': .3, 'text': '一，二三', 'speaker': 'A', 'activeSpeakers': ['A']},
            {'start': 1.1, 'end': 1.3, 'text': '四五。', 'speaker': 'B', 'activeSpeakers': ['B']},
        ])
        self.assertTrue(degenerate)

    def test_sequential_tie_is_unknown_and_simultaneous_activity_is_overlap(self) -> None:
        timeline = speaker_timeline([segment(0, 1, 'A'), segment(1, 3, 'B'), segment(2, 3, 'A')], 4)
        output, _ = self.assemble('one two three', [
            item('one', .8, 1.2), item('two', 2.1, 2.5), item('three', 3.1, 3.5),
        ], timeline)
        self.assertEqual(output, [
            {'start': .8, 'end': 1.2, 'text': 'one ', 'speaker': None, 'activeSpeakers': []},
            {'start': 2.1, 'end': 2.5, 'text': 'two ', 'speaker': None, 'activeSpeakers': ['A', 'B']},
            {'start': 3.1, 'end': 3.5, 'text': 'three', 'speaker': None, 'activeSpeakers': []},
        ])

    def test_window_offset_is_applied_to_alignment_before_speaker_assignment(self) -> None:
        output, _ = self.assemble('hello.', [item('hello', .25, .75)], (
            Activity(0, 30, ('A',)), Activity(30, 34, ('B',)),
        ), start=30, end=34)
        self.assertEqual(output, [
            {'start': 30.25, 'end': 30.75, 'text': 'hello.', 'speaker': 'B', 'activeSpeakers': ['B']},
        ])

    def test_unusable_alignment_fails_without_inventing_timestamps(self) -> None:
        cases = [
            ('hello', [], 'no usable items'),
            ('hello', [item('hello', 0, 0)], 'only zero-duration'),
            ('hello', [item('world', 0, 1)], 'mapped exactly'),
            ('hello world', [item('hello', 0, 1)], 'cover all recognized'),
            ('hello world', [item('hello', 1, 2), item('world', .5, 1)], 'nonmonotone'),
            ('hello', [item('hello', 0, 5)], 'out-of-window'),
            ('hello', [item('hello', float('nan'), 1)], 'invalid'),
            ('hello', [item('hello', -1, 1)], 'invalid'),
        ]
        for text, items, error in cases:
            with self.subTest(text=text, error=error), self.assertRaisesRegex(ValueError, error):
                self.assemble(text, items)


class LanguageTests(unittest.TestCase):
    def test_excludes_languages_whose_required_tokenizer_is_missing(self) -> None:
        model = SimpleNamespace(get_supported_languages=lambda: ['chinese', 'japanese', 'korean'])
        with patch('python.worker.importlib.util.find_spec', return_value=None):
            supported = supported_alignment_languages(model)
        self.assertEqual(supported, {'chinese': 'Chinese'})
        with self.assertRaisesRegex(ValueError, 'does not support.*japanese.*installation'):
            alignment_language('Japanese', None, supported)
        with patch('python.worker.importlib.util.find_spec', return_value=object()):
            self.assertEqual(supported_alignment_languages(model), {
                'chinese': 'Chinese', 'japanese': 'Japanese', 'korean': 'Korean',
            })

    def test_uses_model_language_support_and_normalizes_hint_or_detection(self) -> None:
        supported = supported_alignment_languages(SimpleNamespace(get_supported_languages=lambda: ['chinese', 'english']))
        self.assertEqual(alignment_language(' CHINESE ', ['English', 'Chinese'], supported), 'Chinese')
        self.assertEqual(alignment_language(None, [' English ', 'english'], supported), 'English')
        self.assertEqual(alignment_language(None, 'Chinese', supported), 'Chinese')
        with self.assertRaisesRegex(ValueError, 'does not support.*french'):
            alignment_language('French', None, supported)
        with self.assertRaisesRegex(ValueError, 'ambiguous.*--language'):
            alignment_language(None, ['English', 'Chinese'], supported)
        with self.assertRaisesRegex(ValueError, 'missing.*--language'):
            alignment_language(None, None, supported)
        with self.assertRaisesRegex(ValueError, 'does not declare supported'):
            supported_alignment_languages(SimpleNamespace(get_supported_languages=lambda: None))


if __name__ == '__main__':
    unittest.main()
