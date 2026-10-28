import io
import json
from pathlib import Path
import sys
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
            diarizer = SimpleNamespace(generate=lambda *args, **kwargs: SimpleNamespace(
                segments=[SimpleNamespace(start=i / 2, end=(i + 1) / 2, speaker=str(i % 2))
                          for i in range(8)]))

            def recognize(audio_path, **kwargs):
                with wave.open(str(audio_path)) as source:
                    duration = source.getnframes() / source.getframerate()
                return SimpleNamespace(text='你好，世界。' if duration >= 3 else '碎片',
                                       language=['Chinese'])

            aligner = SimpleNamespace(generate=lambda *args, **kwargs: SimpleNamespace(items=[
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
                code = transcribe({'audio_path': str(audio), 'source_path': str(audio),
                                   'source_duration': 4, 'source_offset': 0,
                                   'selected_duration': 4, 'manifest_path': str(manifest),
                                   'language': 'Chinese'}, response)
            self.assertEqual(code, 0)
            transcript = json.loads(response.getvalue())['transcript']
            self.assertEqual(''.join(segment['text'] for segment in transcript['segments']),
                             '你好，世界。')


if __name__ == '__main__':
    unittest.main()
