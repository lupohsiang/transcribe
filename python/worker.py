from __future__ import annotations

import contextlib
import gc
import importlib.util
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
import wave
import unicodedata
from array import array
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = ROOT / "model-pins.json"
MAX_ASR_SECONDS = 30
MIN_ASR_TAIL_SECONDS = 5
MODEL_NAMES = ("asr", "diarization", "alignment")
DIARIZATION_THRESHOLD = 0.5
# The pinned Qwen3 aligner quantizes timestamps in 80 ms steps.
ALIGNMENT_TIMESTAMP_SECONDS = 0.08
SAMPLE_RATE = 16000
PCM_BLOCK_FRAMES = SAMPLE_RATE


class MediaError(ValueError):
    pass


class PendingAudio:
    def __init__(self, capacity_frames: int):
        self.first_frame = 0
        self.frames_received = 0
        self.capacity_frames = capacity_frames
        self.pcm = bytearray()

    def append(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            raise MediaError("decoder returned an incomplete PCM sample")
        if len(self.pcm) // 2 + len(pcm) // 2 > self.capacity_frames:
            raise ValueError("diarization did not advance within its bounded audio context")
        self.pcm.extend(pcm)
        self.frames_received += len(pcm) // 2

    def next_window(self, stable_frame: int, final: bool = False) -> AudioWindow | None:
        remaining = self.frames_received - self.first_frame
        if not remaining:
            return None
        if final:
            window = recognition_windows(remaining)[0]
            return AudioWindow(self.first_frame, self.first_frame + window.end_frame, SAMPLE_RATE)
        end = self.first_frame + MAX_ASR_SECONDS * SAMPLE_RATE
        if (remaining >= (MAX_ASR_SECONDS + MIN_ASR_TAIL_SECONDS) * SAMPLE_RATE
                and stable_frame >= end + math.ceil(.26 * SAMPLE_RATE)):
            return AudioWindow(self.first_frame, end, SAMPLE_RATE)
        return None

    def write_window(self, path: str, window: AudioWindow) -> None:
        with wave.open(path, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(SAMPLE_RATE)
            target.writeframes(self.pcm[:(window.end_frame - window.first_frame) * 2])

    def discard(self, window: AudioWindow) -> None:
        del self.pcm[:(window.end_frame - self.first_frame) * 2]
        self.first_frame = window.end_frame


class SpeakerProbabilities:
    def __init__(self, frame_samples: int, speakers: int):
        self.frame_samples = frame_samples
        self.speakers = speakers
        self.first_row = 0
        self.rows: list[list[float]] = []
        self.history_rows = math.ceil(.1 * SAMPLE_RATE / frame_samples) + math.ceil(.15 * SAMPLE_RATE / frame_samples) + 1

    @property
    def stable_frame(self) -> int:
        return (self.first_row + len(self.rows)) * self.frame_samples

    def append(self, rows: list[list[float]]) -> None:
        for row in rows:
            if (len(row) != self.speakers
                    or any(not math.isfinite(value) or not 0 <= value <= 1 for value in row)):
                raise ValueError("diarization returned invalid speaker probabilities")
        self.rows.extend(rows)

    def timeline(self, window: AudioWindow) -> tuple[Activity, ...]:
        minimum = math.ceil(.1 * SAMPLE_RATE / self.frame_samples)
        gap = math.floor(.15 * SAMPLE_RATE / self.frame_samples + 1e-9)
        segments = []
        for speaker in range(self.speakers):
            runs: list[tuple[int, int]] = []
            start = None
            for index in range(len(self.rows) + 1):
                active = index < len(self.rows) and self.rows[index][speaker] > DIARIZATION_THRESHOLD
                if active and start is None:
                    start = index
                elif not active and start is not None:
                    if index - start >= minimum:
                        if runs and start - runs[-1][1] <= gap:
                            runs[-1] = (runs[-1][0], index)
                        else:
                            runs.append((start, index))
                    start = None
            for start, end in runs:
                begin = max(window.first_frame, (self.first_row + start) * self.frame_samples)
                finish = min(window.end_frame, (self.first_row + end) * self.frame_samples)
                if finish > begin:
                    segments.append({"start": begin / SAMPLE_RATE, "end": finish / SAMPLE_RATE, "speaker": str(speaker)})
        return speaker_timeline(segments, window.end)

    def discard_before(self, sample: int) -> None:
        first = max(self.first_row, sample // self.frame_samples - self.history_rows)
        del self.rows[:first - self.first_row]
        self.first_row = first


class FFmpegPCM:
    def __init__(self, request: dict[str, Any]):
        args = [request["ffmpeg_path"], "-hide_banner", "-nostdin", "-loglevel", "error", "-i", request["source_path"]]
        if request["source_offset"] > 0:
            args += ["-ss", str(request["source_offset"])]
        args += ["-t", str(request["selected_duration"]), "-map", "0:a:0", "-vn", "-sn", "-dn",
                 "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"]
        self.args = args
        self.stderr = bytearray()

    def __enter__(self) -> FFmpegPCM:
        try:
            self.child = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            raise MediaError(f"could not start FFmpeg: {error}") from error
        self.stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self.stderr_thread.start()
        self.previous_handlers = {}
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                self.previous_handlers[number] = signal.signal(number, self._interrupt)
        return self

    @staticmethod
    def _interrupt(number: int, frame: Any) -> None:
        raise KeyboardInterrupt

    def _drain_stderr(self) -> None:
        while chunk := self.child.stderr.read(4096):
            self.stderr.extend(chunk)
            del self.stderr[:-16384]

    def blocks(self):
        carry = b""
        while chunk := self.child.stdout.read(PCM_BLOCK_FRAMES * 2):
            chunk = carry + chunk
            size = len(chunk) - len(chunk) % 2
            carry = chunk[size:]
            if size:
                yield chunk[:size]
        code = self.child.wait()
        self.stderr_thread.join()
        if code:
            detail = self.stderr.decode("utf-8", errors="replace").strip()
            raise MediaError(f"could not decode audio: {detail or f'FFmpeg exit {code}'}")
        if carry:
            raise MediaError("decoder returned an incomplete PCM sample")

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait()
            self.child.stdout.close()
            self.stderr_thread.join(timeout=1)
            self.child.stderr.close()
        finally:
            for number, handler in self.previous_handlers.items():
                signal.signal(number, handler)


def emit_record(output: Any, record: dict[str, Any]) -> None:
    output.write(json.dumps(record, ensure_ascii=False) + "\n")
    output.flush()


def progress(message: str) -> None:
    print(f"transcribe: {message}", file=sys.stderr, flush=True)


def read_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_pins(path: str | Path = PINS_PATH) -> dict[str, Any]:
    pins = read_json(path)
    if pins.get("schemaVersion") != 1 or not isinstance(pins.get("models"), dict):
        raise ValueError("model-pins.json has an unsupported shape")
    for name in MODEL_NAMES:
        model = pins["models"].get(name)
        if not isinstance(model, dict) or not model.get("id") or not model.get("revision"):
            raise ValueError(f"model-pins.json is missing the {name} model pin")
    return pins


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def setup(manifest_path: Path, pins_path: Path) -> int:
    from huggingface_hub import snapshot_download

    pins = load_pins(pins_path)
    snapshots: dict[str, dict[str, str]] = {}
    for name in MODEL_NAMES:
        model = pins["models"][name]
        progress(f"downloading {model['id']} at {model['revision']}")
        path = snapshot_download(repo_id=model["id"], revision=model["revision"])
        snapshots[name] = {
            "id": model["id"],
            "revision": model["revision"],
            "path": str(Path(path).resolve()),
        }
    atomic_json(
        manifest_path,
        {
            "schemaVersion": 1,
            "models": snapshots,
        },
    )
    progress(f"model setup recorded in {manifest_path}")
    return 0


def field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


@dataclass(frozen=True)
class Activity:
    start: float
    end: float
    speakers: tuple[str, ...]


@dataclass(frozen=True)
class AudioWindow:
    first_frame: int
    end_frame: int
    sample_rate: int

    @property
    def start(self) -> float:
        return self.first_frame / self.sample_rate

    @property
    def end(self) -> float:
        return self.end_frame / self.sample_rate


@dataclass(frozen=True)
class Recognition:
    start: float
    end: float
    text: str
    language: str


@dataclass(frozen=True)
class AlignedUnit:
    start: float
    end: float
    text_start: int
    text_end: int


def speaker_timeline(segments: list[Any], duration: float) -> tuple[Activity, ...]:
    events: dict[float, dict[str, list[str]]] = {
        0.0: {"add": [], "remove": []},
        duration: {"add": [], "remove": []},
    }
    for segment in segments:
        try:
            raw_start = float(field(segment, "start"))
            raw_end = float(field(segment, "end"))
        except (TypeError, ValueError):
            raise ValueError("diarization returned a segment without numeric start and end times") from None
        if not math.isfinite(raw_start) or not math.isfinite(raw_end) or raw_end <= raw_start:
            raise ValueError("diarization returned a segment with invalid time bounds")
        if raw_start < -0.01 or raw_end > duration + 0.1:
            raise ValueError("diarization returned a segment outside the selected audio")
        start = max(0.0, raw_start)
        end = min(duration, raw_end)
        speaker_value = field(segment, "speaker")
        if speaker_value is None or end <= start:
            raise ValueError("diarization returned a segment without a speaker label")
        speaker = str(speaker_value).strip()
        if not speaker:
            raise ValueError("diarization returned an empty speaker label")
        events.setdefault(start, {"add": [], "remove": []})["add"].append(speaker)
        events.setdefault(end, {"add": [], "remove": []})["remove"].append(speaker)

    points = sorted(events)
    counts: dict[str, int] = {}
    intervals: list[Activity] = []
    for index, start in enumerate(points[:-1]):
        event = events[start]
        for speaker in event["remove"]:
            remaining = counts.get(speaker, 0) - 1
            if remaining > 0:
                counts[speaker] = remaining
            else:
                counts.pop(speaker, None)
        for speaker in event["add"]:
            counts[speaker] = counts.get(speaker, 0) + 1
        end = points[index + 1]
        active = tuple(sorted(counts))
        if intervals and intervals[-1].speakers == active:
            intervals[-1] = replace(intervals[-1], end=end)
        else:
            intervals.append(Activity(start, end, active))
    return tuple(intervals)


def recognition_windows(frame_count: int, sample_rate: int = 16000) -> tuple[AudioWindow, ...]:
    if frame_count <= 0 or sample_rate <= 0:
        raise ValueError("converted audio must contain PCM frames")
    boundaries = list(range(0, frame_count, MAX_ASR_SECONDS * sample_rate)) + [frame_count]
    if len(boundaries) > 2 and boundaries[-1] - boundaries[-2] < MIN_ASR_TAIL_SECONDS * sample_rate:
        boundaries[-2] = (boundaries[-3] + boundaries[-1]) // 2
    return tuple(AudioWindow(start, end, sample_rate) for start, end in zip(boundaries, boundaries[1:]))


def supported_alignment_languages(model: Any) -> dict[str, str]:
    languages = model.get_supported_languages()
    if not languages:
        raise ValueError("alignment model does not declare supported languages; run transcribe setup")
    tokenizer_modules = {"japanese": "nagisa", "korean": "soynlp"}
    supported = {}
    for value in languages:
        language = str(value).strip().lower()
        module = tokenizer_modules.get(language)
        if module is None or importlib.util.find_spec(module) is not None:
            supported[language] = str(value).strip().title()
    return supported


def alignment_language(hint: Any, detected: Any, supported: dict[str, str]) -> str:
    values = [hint] if hint else detected if isinstance(detected, list) else [detected]
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("alignment language is missing; pass --language with a supported language name")
    languages = {value.strip().lower() for value in values}
    if len(languages) != 1:
        raise ValueError("alignment language is ambiguous; pass --language with one supported language name")
    language = languages.pop()
    if language not in supported:
        raise ValueError(
            f"alignment does not support language {language!r} in this installation; available languages: "
            + ", ".join(sorted(supported.values()))
        )
    return supported[language]


def retained_characters(text: str) -> tuple[str, list[int]]:
    indices = [index for index, char in enumerate(text)
               if char == "'" or unicodedata.category(char)[0] in ("L", "N")]
    return "".join(text[index] for index in indices), indices


def aligned_units(recognition: Recognition, items: list[Any]) -> tuple[tuple[AlignedUnit, ...], bool]:
    retained, indices = retained_characters(recognition.text)
    if not retained or not items:
        raise ValueError("alignment returned no usable items for nonempty recognition")
    units: list[AlignedUnit] = []
    cursor = 0
    previous_end = 0.0
    duration = recognition.end - recognition.start
    latest_end = duration + ALIGNMENT_TIMESTAMP_SECONDS / 2 + 1e-9
    for item in items:
        text = field(item, "text")
        if not isinstance(text, str):
            raise ValueError("alignment returned an item without text")
        token, _ = retained_characters(text)
        next_cursor = cursor + len(token)
        if not token or retained[cursor:next_cursor] != token:
            raise ValueError("alignment items cannot be mapped exactly to the recognized text")
        try:
            start = float(field(item, "start_time"))
            end = float(field(item, "end_time"))
        except (TypeError, ValueError):
            raise ValueError("alignment returned nonnumeric timestamps") from None
        if (not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or start > duration or end < start or end > latest_end):
            raise ValueError("alignment returned invalid or out-of-window timestamps")
        if start < previous_end:
            raise ValueError("alignment returned nonmonotone timestamps")
        text_start = 0 if cursor == 0 else indices[cursor]
        text_end = indices[next_cursor] if next_cursor < len(indices) else len(recognition.text)
        units.append(AlignedUnit(
            recognition.start + start, min(recognition.end, recognition.start + end), text_start, text_end,
        ))
        cursor = next_cursor
        previous_end = end
    if cursor != len(retained):
        raise ValueError("alignment items do not cover all recognized text")

    positive: list[AlignedUnit] = []
    leading_start = units[0].text_start
    has_degenerate = False
    for unit in units:
        if unit.start == unit.end:
            has_degenerate = True
            if positive:
                positive[-1] = replace(positive[-1], text_end=unit.text_end)
        else:
            positive.append(replace(unit, text_start=leading_start) if not positive else unit)
    if not positive:
        raise ValueError("alignment returned only zero-duration items for nonempty recognition")
    return tuple(positive), has_degenerate


def assemble_window(
    recognition: Recognition, units: tuple[AlignedUnit, ...], timeline: tuple[Activity, ...]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    activity_index = 0
    previous_assignment: int | None = None
    for unit in units:
        while activity_index < len(timeline) and timeline[activity_index].end <= unit.start:
            activity_index += 1
        largest = 0.0
        candidates: list[int] = []
        index = activity_index
        while index < len(timeline) and timeline[index].start < unit.end:
            activity = timeline[index]
            overlap = min(unit.end, activity.end) - max(unit.start, activity.start)
            if math.isclose(overlap, largest, abs_tol=1e-8):
                candidates.append(index)
            elif overlap > largest:
                largest = overlap
                candidates = [index]
            index += 1
        sets = {timeline[index].speakers for index in candidates}
        active = next(iter(sets)) if len(sets) == 1 else ()
        assignment = candidates[0] if len(candidates) == 1 else None
        text = recognition.text[unit.text_start:unit.text_end]
        if output and assignment is not None and assignment == previous_assignment:
            output[-1]["end"] = unit.end
            output[-1]["text"] += text
        else:
            output.append({
                "start": unit.start,
                "end": unit.end,
                "text": text,
                "speaker": active[0] if len(active) == 1 else None,
                "activeSpeakers": list(active),
            })
        previous_assignment = assignment
    return output


def clear_mlx_cache() -> None:
    gc.collect()
    try:
        import mlx.core as mx

        mx.clear_cache()
    except ImportError:
        pass


def transcribe(request: dict[str, Any], protocol_output: Any) -> int:
    required = ("source_path", "source_duration", "source_offset", "selected_duration", "manifest_path", "ffmpeg_path")
    if any(key not in request for key in required):
        raise ValueError("worker request is missing required fields")
    source_duration = float(request["source_duration"])
    source_offset = float(request["source_offset"])
    selected_duration = float(request["selected_duration"])
    if (not all(math.isfinite(value) for value in (source_duration, source_offset, selected_duration))
            or source_duration <= 0 or source_offset < 0 or source_offset >= source_duration or selected_duration <= 0
            or source_offset + selected_duration > source_duration + 0.01):
        raise ValueError("worker request contains invalid audio times")
    selection_end = min(source_duration, source_offset + selected_duration)
    pins = load_pins()
    lock = read_json(request["manifest_path"])
    if lock.get("schemaVersion") != 1:
        raise ValueError("model setup manifest has an unsupported version")
    for name in MODEL_NAMES:
        pinned = pins["models"][name]
        installed = lock.get("models", {}).get(name, {})
        if installed.get("id") != pinned["id"] or installed.get("revision") != pinned["revision"]:
            raise ValueError(f"installed {name} model does not match model-pins.json; run transcribe setup")
        if not Path(installed.get("path", "")).is_dir():
            raise RuntimeError(f"MODEL_MISSING: {name} model files are missing; run transcribe setup")

    from mlx_audio.vad import load as load_diarization
    from mlx_audio.stt import load as load_asr

    progress("loading streaming speaker, recognition and alignment models")
    with contextlib.redirect_stdout(sys.stderr):
        diarizer = load_diarization(lock["models"]["diarization"]["path"], strict=True)
        asr = load_asr(lock["models"]["asr"]["path"])
        aligner = load_asr(lock["models"]["alignment"]["path"])
    supported = supported_alignment_languages(aligner)
    language_hint = request.get("language")
    if language_hint:
        language_hint = alignment_language(language_hint, None, supported)
    state = diarizer.init_streaming_state()
    config = diarizer.config
    hop = config.processor_config.hop_length
    frame_samples = hop * config.output_subsampling_factor
    lookahead = ((config.modules_config.chunk_len + config.modules_config.chunk_right_context)
                 * config.encoder_config.subsampling_factor * hop + config.processor_config.n_fft)
    pending = PendingAudio((MAX_ASR_SECONDS + MIN_ASR_TAIL_SECONDS + 1) * SAMPLE_RATE + lookahead)
    probabilities = SpeakerProbabilities(frame_samples, config.num_speakers)
    emit_record(protocol_output, {"type": "header", "protocol": 1, "transcript": {
        "schemaVersion": 1,
        "source": {"path": str(Path(request["source_path"]).resolve()), "duration": source_duration,
                   "selection": {"start": source_offset, "end": selection_end}},
        "models": {name: f"{lock['models'][name]['id']}@{lock['models'][name]['revision']}" for name in MODEL_NAMES},
        "timing": "forced-aligned", "segments": [], "warnings": [],
    }})
    window_count = empty_windows = degenerate_windows = segment_count = 0
    has_overlap = has_unknown = False

    def feed(pcm: bytes, final: bool = False) -> None:
        nonlocal state
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        with contextlib.redirect_stdout(sys.stderr):
            result, state = diarizer.feed([sample / 32768 for sample in samples], state,
                                         sample_rate=SAMPLE_RATE, final=final)
        rows = result.speaker_probs.tolist()
        probabilities.append(rows)
        del result
        clear_mlx_cache()

    def recognize_ready(chunk_path: str, final: bool = False) -> None:
        nonlocal window_count, empty_windows, degenerate_windows, segment_count, has_overlap, has_unknown
        while window := pending.next_window(probabilities.stable_frame, final):
            window_count += 1
            progress(f"transcribing and aligning window {window_count} ({window.start:.3f}-{window.end:.3f}s)")
            timeline = probabilities.timeline(window)
            has_overlap = has_overlap or any(len(activity.speakers) > 1 for activity in timeline)
            pending.write_window(chunk_path, window)
            try:
                kwargs = {"language": language_hint} if language_hint else {}
                with contextlib.redirect_stdout(sys.stderr):
                    result = asr.generate(chunk_path, **kwargs)
                text = field(result, "text")
                if not isinstance(text, str):
                    raise ValueError("speech recognition returned no text field")
                if text.strip():
                    language = alignment_language(language_hint, field(result, "language"), supported)
                    recognition = Recognition(window.start, window.end, text, language)
                    del result
                    clear_mlx_cache()
                    with contextlib.redirect_stdout(sys.stderr):
                        alignment = aligner.generate(audio=chunk_path, text=text, language=language)
                    units, degenerate = aligned_units(recognition, list(alignment.items))
                    del alignment
                    degenerate_windows += int(degenerate)
                    for segment in assemble_window(recognition, units, timeline):
                        has_unknown = has_unknown or not segment["activeSpeakers"]
                        segment["start"] += source_offset
                        segment["end"] = min(selection_end, source_offset + segment["end"])
                        emit_record(protocol_output, {"type": "segment", "segment": segment})
                        segment_count += 1
                else:
                    empty_windows += 1
                    del result
            except ValueError as error:
                raise ValueError(f"window {window_count} ({window.start:.3f}-{window.end:.3f}s): {error}") from error
            finally:
                Path(chunk_path).unlink(missing_ok=True)
                clear_mlx_cache()
            pending.discard(window)
            probabilities.discard_before(window.end_frame)

    try:
        with FFmpegPCM(request) as decoder, tempfile.TemporaryDirectory(prefix="transcribe-") as scratch:
            chunk_path = os.path.join(scratch, "window.wav")
            for pcm in decoder.blocks():
                pending.append(pcm)
                if pending.frames_received > math.ceil(selected_duration * SAMPLE_RATE + 1e-6):
                    raise MediaError("decoded audio extends past the selected source range")
                feed(pcm)
                recognize_ready(chunk_path)
            if not pending.frames_received:
                raise MediaError("the selected range contains no audio samples")
            feed(b"", final=True)
            recognize_ready(chunk_path, final=True)
    finally:
        del diarizer, asr, aligner, state
        clear_mlx_cache()
    warnings = [
        "Speaker labels are generic arrival-order IDs and do not identify people.",
        "Timestamps are model-derived forced alignment estimates, not verified word-level accuracy.",
    ]
    if empty_windows:
        warnings.append(f"No text was recognized in {empty_windows} of {window_count} continuous audio windows.")
    if degenerate_windows:
        warnings.append(f"Zero-duration alignment items in {degenerate_windows} windows were merged with adjacent timed text.")
    if has_overlap:
        warnings.append("Overlapping speaker activity retains the simultaneous speaker set without a single speaker label.")
    if has_unknown:
        warnings.append("Some recognized text has unknown speaker attribution.")
    if source_offset > 0 or selected_duration < source_duration - 0.01:
        warnings.append("Speaker IDs are scoped to this selected audio range.")
    emit_record(protocol_output, {"type": "done", "count": segment_count, "warnings": warnings})
    return 0


def main() -> int:
    protocol_output = sys.stdout
    if len(sys.argv) == 4 and sys.argv[1] == "setup":
        try:
            return setup(Path(sys.argv[2]), Path(sys.argv[3]))
        except Exception as error:
            print(f"transcribe: setup failed: {error}", file=sys.stderr, flush=True)
            return 1

    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("worker request must be a JSON object")
        with contextlib.redirect_stdout(sys.stderr):
            return transcribe(request, protocol_output)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        message = str(error)
        code = "MEDIA_ERROR" if isinstance(error, MediaError) else "MODEL_MISSING" if message.startswith("MODEL_MISSING:") else "WORKER_ERROR"
        if code == "MODEL_MISSING":
            message = message.partition(":")[2].strip()
        emit_record(protocol_output, {"type": "error", "error": {"code": code, "message": message}})
        print(f"transcribe: {message}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
