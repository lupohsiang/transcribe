import io
import json
from pathlib import Path
import sys
import shutil
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from python.worker import load_pins, transcribe


class ContextRecognitionTests(unittest.TestCase):
    def test_rapid_speaker_changes_do_not_fragment_recognition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / 'speech.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b'\x01\x00' * 64000)
            models = {}
            for name, pin in load_pins()['models'].items():
                snapshot = root / name
                snapshot.mkdir()
                models[name] = {**pin, 'path': str(snapshot)}
            manifest = root / 'models.lock.json'
            manifest.write_text(json.dumps({'schemaVersion': 1, 'models': models}))
            def feed(samples, state, **kwargs):
                rows = [[.9, .1] if (state.frames + index) // 50 % 2 == 0 else [.1, .9]
                        for index in range(len(samples) // 160)]
                state.frames += len(rows)
                return SimpleNamespace(speaker_probs=SimpleNamespace(tolist=lambda: rows)), state

            diarizer = SimpleNamespace(
                config=SimpleNamespace(
                    processor_config=SimpleNamespace(hop_length=160, n_fft=512),
                    output_subsampling_factor=1, num_speakers=2,
                    encoder_config=SimpleNamespace(subsampling_factor=8),
                    modules_config=SimpleNamespace(chunk_len=340, chunk_right_context=40)),
                init_streaming_state=lambda: SimpleNamespace(frames=0), feed=feed)

            def recognize(audio_path, **kwargs):
                with wave.open(str(audio_path)) as source:
                    duration = source.getnframes() / source.getframerate()
                return SimpleNamespace(text='你好，世界。' if duration >= 3 else '碎片',
                                       language=['Chinese'])

            aligner = SimpleNamespace(get_supported_languages=lambda: ['chinese', 'english'], generate=lambda *args, **kwargs: SimpleNamespace(items=[
                SimpleNamespace(text='你', start_time=0.05, end_time=0.2),
                SimpleNamespace(text='好', start_time=0.2, end_time=0.4),
                SimpleNamespace(text='世', start_time=0.55, end_time=0.7),
                SimpleNamespace(text='界', start_time=0.7, end_time=0.9),
            ]))
            vad = ModuleType('mlx_audio.vad')
            vad.load = lambda *args, **kwargs: diarizer
            stt = ModuleType('mlx_audio.stt')
            stt.load = lambda path, **kwargs: aligner if Path(path).name in ('alignment', 'aligner') else SimpleNamespace(generate=recognize)
            mlx = ModuleType('mlx')
            core = ModuleType('mlx.core')
            core.clear_cache = lambda: None
            mlx.core = core
            response = io.StringIO()
            with patch.dict(sys.modules, {'mlx_audio.vad': vad, 'mlx_audio.stt': stt,
                                          'mlx': mlx, 'mlx.core': core}):
                code = transcribe({'ffmpeg_path': shutil.which('ffmpeg'), 'source_path': str(audio),
                                   'source_duration': 4, 'source_offset': 0,
                                   'selected_duration': 4, 'manifest_path': str(manifest),
                                   'language': 'Chinese'}, response)
            self.assertEqual(code, 0)
            records = [json.loads(line) for line in response.getvalue().splitlines()]
            self.assertEqual(records[0]['type'], 'header')
            self.assertEqual(records[-1], {'type': 'done', 'count': 2, 'warnings': [
                'Speaker labels are generic arrival-order IDs and do not identify people.',
                'Timestamps are model-derived forced alignment estimates, not verified word-level accuracy.',
            ]})
            segments = [record['segment'] for record in records if record['type'] == 'segment']
            self.assertEqual(''.join(segment['text'] for segment in segments),
                             '你好，世界。')
            self.assertEqual([segment['speaker'] for segment in segments], ['0', '1'])


if __name__ == '__main__':
    unittest.main()
