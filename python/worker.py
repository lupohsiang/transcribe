from __future__ import annotations

import contextlib
import gc
import importlib.util
import json
import math
import os
import sys
import tempfile
import wave
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = ROOT / "model-pins.json"
MAX_ASR_SECONDS = 30
MIN_ASR_TAIL_SECONDS = 5
MODEL_NAMES = ("asr", "diarization", "alignment")
DIARIZATION_THRESHOLD = 0.5


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


def write_wave_chunk(source_path: str, output_path: str, window: AudioWindow) -> None:
    with wave.open(source_path, "rb") as source:
        source.setpos(window.first_frame)
        frames = source.readframes(window.end_frame - window.first_frame)
        with wave.open(output_path, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(window.sample_rate)
            target.writeframes(frames)


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
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or end > duration:
            raise ValueError("alignment returned invalid or out-of-window timestamps")
        if start < previous_end:
            raise ValueError("alignment returned nonmonotone timestamps")
        text_start = 0 if cursor == 0 else indices[cursor]
        text_end = indices[next_cursor] if next_cursor < len(indices) else len(recognition.text)
        units.append(AlignedUnit(recognition.start + start, recognition.start + end, text_start, text_end))
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
    required = ("audio_path", "source_path", "source_duration", "source_offset", "selected_duration", "manifest_path")
    if any(key not in request for key in required):
        raise ValueError("worker request is missing required fields")
    source_duration = float(request["source_duration"])
    source_offset = float(request["source_offset"])
    selected_duration = float(request["selected_duration"])
    if (not all(math.isfinite(value) for value in (source_duration, source_offset, selected_duration))
            or source_offset < 0 or selected_duration <= 0
            or source_offset + selected_duration > source_duration + 0.01):
        raise ValueError("worker request contains invalid audio times")
    with wave.open(request["audio_path"], "rb") as audio:
        if audio.getnchannels() != 1 or audio.getframerate() != 16000 or audio.getsampwidth() != 2:
            raise ValueError("converted audio must be mono 16 kHz signed 16-bit PCM")
        windows = recognition_windows(audio.getnframes(), audio.getframerate())
    duration = windows[-1].end
    selection_end = min(source_duration, source_offset + selected_duration)
    if duration > selection_end - source_offset + 1 / 16000:
        raise ValueError("converted audio extends past the selected source range")

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

    progress("loading diarization model")
    with contextlib.redirect_stdout(sys.stderr):
        diarization_model = load_diarization(lock["models"]["diarization"]["path"], strict=True)
    progress("analyzing the selected audio once")
    with contextlib.redirect_stdout(sys.stderr):
        diarization = diarization_model.generate(
            request["audio_path"], threshold=DIARIZATION_THRESHOLD, min_duration=0.1, merge_gap=0.15,
        )
    timeline = speaker_timeline(list(diarization.segments), duration)
    del diarization, diarization_model
    clear_mlx_cache()
    progress(f"found {len(timeline)} speaker activity intervals")

    output_segments: list[dict[str, Any]] = []
    empty_windows = 0
    degenerate_windows = 0
    progress("loading speech recognition and alignment models")
    with contextlib.redirect_stdout(sys.stderr):
        asr_model = load_asr(lock["models"]["asr"]["path"])
        alignment_model = load_asr(lock["models"]["alignment"]["path"])
    supported = supported_alignment_languages(alignment_model)
    language_hint = request.get("language")
    if language_hint:
        language_hint = alignment_language(language_hint, None, supported)
    try:
        with tempfile.TemporaryDirectory(prefix="asr-", dir=str(Path(request["audio_path"]).parent)) as chunk_directory:
            chunk_path = os.path.join(chunk_directory, "window.wav")
            for index, window in enumerate(windows, start=1):
                write_wave_chunk(request["audio_path"], chunk_path, window)
                progress(f"transcribing and aligning window {index} of {len(windows)}")
                try:
                    kwargs = {"language": language_hint} if language_hint else {}
                    with contextlib.redirect_stdout(sys.stderr):
                        result = asr_model.generate(chunk_path, **kwargs)
                    text = field(result, "text")
                    if not isinstance(text, str):
                        raise ValueError("speech recognition returned no text field")
                    if not text.strip():
                        empty_windows += 1
                        del result
                        continue
                    language = alignment_language(language_hint, field(result, "language"), supported)
                    recognition = Recognition(window.start, window.end, text, language)
                    del result
                    clear_mlx_cache()
                    with contextlib.redirect_stdout(sys.stderr):
                        alignment = alignment_model.generate(audio=chunk_path, text=text, language=language)
                    units, has_degenerate = aligned_units(recognition, list(alignment.items))
                    del alignment
                    degenerate_windows += int(has_degenerate)
                    output_segments.extend(assemble_window(recognition, units, timeline))
                except ValueError as error:
                    raise ValueError(f"window {index} ({window.start:.3f}-{window.end:.3f}s): {error}") from error
                finally:
                    os.unlink(chunk_path)
                    clear_mlx_cache()
    finally:
        del asr_model, alignment_model
        clear_mlx_cache()

    for segment in output_segments:
        segment["start"] += source_offset
        segment["end"] = min(selection_end, source_offset + segment["end"])
    warnings = [
        "Speaker labels are generic arrival-order IDs and do not identify people.",
        "Timestamps are model-derived forced alignment estimates, not verified word-level accuracy.",
    ]
    if empty_windows:
        warnings.append(f"No text was recognized in {empty_windows} of {len(windows)} continuous audio windows.")
    if degenerate_windows:
        warnings.append(f"Zero-duration alignment items in {degenerate_windows} windows were merged with adjacent timed text.")
    if any(len(activity.speakers) > 1 for activity in timeline):
        warnings.append("Overlapping speaker activity retains the simultaneous speaker set without a single speaker label.")
    if any(not segment["activeSpeakers"] for segment in output_segments):
        warnings.append("Some recognized text has unknown speaker attribution.")
    if source_offset > 0 or selected_duration < source_duration - 0.01:
        warnings.append("Speaker IDs are scoped to this selected audio range.")
    transcript = {
        "schemaVersion": 1,
        "source": {
            "path": str(Path(request["source_path"]).resolve()),
            "duration": source_duration,
            "selection": {"start": source_offset, "end": selection_end},
        },
        "models": {name: f"{lock['models'][name]['id']}@{lock['models'][name]['revision']}" for name in MODEL_NAMES},
        "timing": "forced-aligned",
        "segments": output_segments,
        "warnings": warnings,
    }
    json.dump({"ok": True, "transcript": transcript}, protocol_output, ensure_ascii=False)
    protocol_output.write("\n")
    protocol_output.flush()
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
    except Exception as error:
        message = str(error)
        code = "MODEL_MISSING" if message.startswith("MODEL_MISSING:") else "WORKER_ERROR"
        if code == "MODEL_MISSING":
            message = message.partition(":")[2].strip()
        json.dump({"ok": False, "error": {"code": code, "message": message}}, protocol_output)
        protocol_output.write("\n")
        protocol_output.flush()
        print(f"transcribe: {message}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
