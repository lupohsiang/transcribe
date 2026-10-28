from __future__ import annotations

import contextlib
import gc
import json
import math
import os
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = ROOT / "model-pins.json"
MAX_ASR_SECONDS = 25.0
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
    for name in ("asr", "diarization"):
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
    for name in ("asr", "diarization"):
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


def split_intervals(
    segments: list[Any], duration: float
) -> tuple[list[tuple[float, float, tuple[str, ...]]], bool, bool]:
    events: dict[float, dict[str, list[str]]] = {}
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
    intervals: list[tuple[float, float, tuple[str, ...]]] = []
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
        if end <= start or not active:
            continue
        if intervals and intervals[-1][2] == active and math.isclose(intervals[-1][1], start, abs_tol=1e-7):
            previous_start, _, previous_active = intervals[-1]
            intervals[-1] = (previous_start, end, previous_active)
        else:
            intervals.append((start, end, active))

    chunks: list[tuple[float, float, tuple[str, ...]]] = []
    has_overlap = any(len(active) > 1 for _, _, active in intervals)
    has_split = False
    for start, end, active in intervals:
        has_split = has_split or end - start > MAX_ASR_SECONDS
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + MAX_ASR_SECONDS)
            chunks.append((cursor, chunk_end, active))
            cursor = chunk_end
    return chunks, has_overlap, has_split


def write_wave_chunk(source_path: str, output_path: str, start: float, end: float) -> None:
    with wave.open(source_path, "rb") as source:
        rate = source.getframerate()
        if source.getnchannels() != 1 or rate != 16000 or source.getsampwidth() != 2:
            raise ValueError("converted audio must be mono 16 kHz signed 16-bit PCM")
        first_frame = max(0, int(round(start * rate)))
        frame_count = max(1, int(round((end - start) * rate)))
        source.setpos(first_frame)
        frames = source.readframes(frame_count)
        with wave.open(output_path, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(frames)


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
    if not all(math.isfinite(value) for value in (source_duration, source_offset, selected_duration)):
        raise ValueError("worker request contains invalid audio times")

    pins = load_pins()
    lock = read_json(request["manifest_path"])
    if lock.get("schemaVersion") != 1:
        raise ValueError("model setup manifest has an unsupported version")
    for name in ("asr", "diarization"):
        pinned = pins["models"][name]
        installed = lock.get("models", {}).get(name, {})
        if installed.get("id") != pinned["id"] or installed.get("revision") != pinned["revision"]:
            raise ValueError(f"installed {name} model does not match model-pins.json")
        if not Path(installed.get("path", "")).is_dir():
            raise RuntimeError(f"MODEL_MISSING: {name} model files are missing; run transcribe setup")

    from mlx_audio.vad import load as load_diarization

    diarization_path = lock["models"]["diarization"]["path"]
    progress("loading diarization model")
    with contextlib.redirect_stdout(sys.stderr):
        diarization_model = load_diarization(diarization_path, strict=True)
    progress("analyzing the selected audio once")
    with contextlib.redirect_stdout(sys.stderr):
        diarization = diarization_model.generate(
            request["audio_path"],
            threshold=DIARIZATION_THRESHOLD,
            min_duration=0.1,
            merge_gap=0.15,
        )
    intervals, has_overlap, has_split = split_intervals(list(diarization.segments), selected_duration)
    del diarization, diarization_model
    clear_mlx_cache()
    progress(f"found {len(intervals)} bounded speaker intervals")

    output_segments: list[dict[str, Any]] = []
    if intervals:
        from mlx_audio.stt import load as load_asr

        asr_path = lock["models"]["asr"]["path"]
        progress("loading speech recognition model")
        with contextlib.redirect_stdout(sys.stderr):
            asr_model = load_asr(asr_path)
        with tempfile.TemporaryDirectory(prefix="asr-", dir=str(Path(request["audio_path"]).parent)) as chunk_directory:
            for index, (start, end, active) in enumerate(intervals, start=1):
                chunk_path = os.path.join(chunk_directory, f"{index:06d}.wav")
                write_wave_chunk(request["audio_path"], chunk_path, start, end)
                progress(f"transcribing interval {index} of {len(intervals)}")
                kwargs: dict[str, Any] = {}
                language = request.get("language")
                if language:
                    kwargs["language"] = language
                with contextlib.redirect_stdout(sys.stderr):
                    result = asr_model.generate(chunk_path, **kwargs)
                text = str(field(result, "text") or "").strip()
                if not text:
                    continue
                absolute_start = source_offset + start
                absolute_end = min(source_duration, source_offset + end)
                active_speakers = list(active)
                output_segments.append(
                    {
                        "start": absolute_start,
                        "end": absolute_end,
                        "text": text,
                        "speaker": active_speakers[0] if len(active_speakers) == 1 else None,
                        "activeSpeakers": active_speakers,
                    }
                )
        del asr_model
        clear_mlx_cache()

    warnings = [
        "Speaker labels are generic arrival-order IDs and do not identify people.",
        "Timestamps mark speaker-turn or bounded ASR slice extents, not word-level alignment.",
    ]
    empty_intervals = len(intervals) - len(output_segments)
    if empty_intervals:
        warning = f"No text was recognized in {empty_intervals} of {len(intervals)} diarized intervals; those intervals were omitted."
        warnings.append(warning)
        progress(f"warning: {warning}")
    if has_overlap:
        warnings.append("Overlapping speaker activity is marked as unattributed.")
    if has_split:
        warnings.append("Long speaker intervals were split at 25-second ASR boundaries.")
    if source_offset > 0 or selected_duration < source_duration - 0.01:
        warnings.append("Speaker IDs are scoped to this selected audio range.")
    transcript = {
        "schemaVersion": 1,
        "source": {
            "path": str(Path(request["source_path"]).resolve()),
            "duration": source_duration,
            "selection": {
                "start": source_offset,
                "end": min(source_duration, source_offset + selected_duration),
            },
        },
        "models": {
            "asr": f"{lock['models']['asr']['id']}@{lock['models']['asr']['revision']}",
            "diarization": f"{lock['models']['diarization']['id']}@{lock['models']['diarization']['revision']}",
        },
        "timing": "speaker-turn",
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
