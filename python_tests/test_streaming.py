from pathlib import Path
import shutil
import tempfile
import unittest

from python.worker import Activity, AudioWindow, FFmpegPCM, MediaError, PendingAudio, SpeakerProbabilities, transcribe


class StreamingAudioTests(unittest.TestCase):
    def test_rejects_empty_source_and_selection_at_eof_before_loading_models(self):
        request = {'source_path': 'sample.wav', 'source_duration': 1, 'source_offset': 0,
                   'selected_duration': .001, 'manifest_path': 'models.lock.json', 'ffmpeg_path': 'ffmpeg'}
        for changes in ({'source_duration': 0}, {'source_offset': 1}):
            with self.assertRaisesRegex(ValueError, 'invalid audio times'):
                transcribe({**request, **changes}, io.StringIO())

    def test_holds_uncommitted_tail_and_rebalances_at_eof(self):
        audio = PendingAudio(65 * 16000)
        audio.append(b'\0\0' * (61 * 16000 + 1))
        first = audio.next_window(60 * 16000)
        self.assertEqual((first.first_frame, first.end_frame), (0, 480000))
        audio.discard(first)
        self.assertIsNone(audio.next_window(61 * 16000))
        second = audio.next_window(61 * 16000, final=True)
        self.assertEqual((second.first_frame, second.end_frame), (480000, 728000))
        audio.discard(second)
        third = audio.next_window(61 * 16000, final=True)
        self.assertEqual((third.first_frame, third.end_frame), (728000, 976001))
        audio.discard(third)
        self.assertEqual(len(audio.pcm), 0)

    def test_ten_minute_stream_keeps_only_bounded_pending_audio(self):
        audio = PendingAudio(67 * 16000)
        windows = []
        maximum = 0
        for second in range(600):
            audio.append(b'\0\0' * 16000)
            maximum = max(maximum, len(audio.pcm) // 2)
            while window := audio.next_window(max(0, (second + 1 - 30) * 16000)):
                windows.append((window.first_frame // 16000, window.end_frame // 16000))
                audio.discard(window)
        while window := audio.next_window(600 * 16000, final=True):
            windows.append((window.first_frame // 16000, window.end_frame // 16000))
            audio.discard(window)
        self.assertEqual(windows, [(start, start + 30) for start in range(0, 600, 30)])
        self.assertEqual(maximum, 61 * 16000)
        self.assertEqual(len(audio.pcm), 0)

    def test_stalled_diarizer_fails_instead_of_buffering_the_recording(self):
        audio = PendingAudio(2 * 16000)
        audio.append(b'\0\0' * 16000)
        self.assertEqual(audio.frames_received, 16000)
        with self.assertRaisesRegex(ValueError, 'bounded audio context'):
            audio.append(b'\0\0' * (2 * 16000))

    def test_real_decoder_rejects_invalid_media_and_decodes_little_endian_pcm(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.mp4'
            path.write_bytes(b'not a media file')
            request = {'ffmpeg_path': shutil.which('ffmpeg'), 'source_path': str(path),
                       'source_offset': 0, 'selected_duration': 1}
            with self.assertRaisesRegex(MediaError, 'could not decode audio'):
                with FFmpegPCM(request) as decoder:
                    list(decoder.blocks())
            wave_path = Path(directory) / 'sample.wav'
            audio = PendingAudio(16000)
            audio.append(b'\x01\x00' * 16000)
            audio.write_window(str(wave_path), AudioWindow(0, 16000, 16000))
            request['source_path'] = str(wave_path)
            with FFmpegPCM(request) as decoder:
                data = b''.join(decoder.blocks())
            self.assertEqual(data, b'\x01\x00' * 16000)


class StreamingSpeakerTests(unittest.TestCase):
    def test_filters_short_runs_before_merging_across_feed_boundaries(self):
        rows = [[.9, .1] if index < 100 else [.1, .1] for index in range(200)]
        for index in range(45, 50):
            rows[index] = [.1, .1]
        for index in list(range(70, 75)) + list(range(100, 130)):
            rows[index][1] = .9
        probabilities = SpeakerProbabilities(160, 2)
        probabilities.append(rows[:47])
        probabilities.append(rows[47:73])
        probabilities.append(rows[73:107])
        probabilities.append(rows[107:])
        self.assertEqual(probabilities.timeline(AudioWindow(0, 32000, 16000)), (
            Activity(0, 1, ('0',)), Activity(1, 1.3, ('1',)), Activity(1.3, 2, ()),
        ))

    def test_discard_retains_boundary_history_and_speaker_identity(self):
        probabilities = SpeakerProbabilities(160, 2)
        probabilities.append([[.9, .1]] * 3000 + [[.1, .9]] * 15 + [[.9, .1]] * 85)
        first = probabilities.timeline(AudioWindow(0, 480000, 16000))
        self.assertEqual(first, (Activity(0, 30, ('0',)),))
        probabilities.discard_before(480000)
        self.assertEqual(len(probabilities.rows), 126)
        self.assertEqual(probabilities.timeline(AudioWindow(480000, 496000, 16000)), (
            Activity(0, 30, ()), Activity(30, 30.15, ('0', '1')),
            Activity(30.15, 31, ('0',)),
        ))

    def test_probabilities_must_be_valid_and_an_empty_timeline_is_unknown(self):
        probabilities = SpeakerProbabilities(160, 2)
        self.assertEqual(probabilities.timeline(AudioWindow(0, 160, 16000)), (Activity(0, .01, ()),))
        probabilities.append([[.9, .1]] * 10)
        self.assertEqual(probabilities.timeline(AudioWindow(0, 1600, 16000)), (Activity(0, .1, ('0',)),))
        for rows in ([[float('nan'), .1]], [[.9]], [[1.1, 0]]):
            with self.assertRaisesRegex(ValueError, 'invalid speaker probabilities'):
                probabilities.append(rows)


if __name__ == '__main__':
    unittest.main()
import io
