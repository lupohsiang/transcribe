"""Prepare small, pinned local evaluation manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import wave


ASCEND_URL = "https://huggingface.co/datasets/CAiRE/ASCEND/resolve/737e9800ae31be9932ba8464c80366559bd28424/main/test-00000-of-00001.parquet"
ASCEND_REVISION = "737e9800ae31be9932ba8464c80366559bd28424"
ASCEND_SHA256 = "a4c81d2b5ed6124f052089a695972808c16e0ce0c365ec9773c5d1a8fcf043a7"
AMI_AUDIO_SHA256 = "3e2560b19bee6952c7c7ce041b0f1ea8a7ea9468044c4eea79d2a2c67e24ab0f"
AMI_ANNOTATION_SHA256 = "b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def copy_new(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)


def write_jsonl_new(path: Path, cases: list[dict]) -> None:
    payload = "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases)
    write_new(path, payload.encode("utf-8"))


def make_case(case_id: str, corpus: str, group: str, split: str, tuning: bool,
              audio_path: str, audio_hash: str, duration: float, start: float, end: float,
              language_hint: str | None, reference_language: str, chinese_script: str,
              ref_path: str, ref_hash: str, ref_status: str, source_id: str,
              revision: str | None, license_name: str | None, source_url: str | None,
              detail: dict, reviewer: dict | None = None) -> dict:
    reference = {"path": ref_path, "sha256": ref_hash, "status": ref_status}
    if reviewer is not None:
        reference["reviewer"] = reviewer
    return {
        "id": case_id,
        "corpus": corpus,
        "group": group,
        "split": split,
        "used_for_tuning": tuning,
        "audio": {"path": audio_path, "sha256": audio_hash, "duration": duration},
        "selection": {"start": start, "end": end},
        "language_hint": language_hint,
        "reference_language": reference_language,
        "chinese_script": chinese_script,
        "reference": reference,
        "provenance": {
            "source_id": source_id,
            "source_revision": revision,
            "license": license_name,
            "source_url": source_url,
            "detail": detail,
        },
    }


def check_ascend_source(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"ASCEND parquet not found: {path}")
    actual = sha256_file(path)
    if actual != ASCEND_SHA256:
        raise ValueError(f"ASCEND parquet SHA-256 mismatch: expected {ASCEND_SHA256}, got {actual}")


def ascend_cases(args: argparse.Namespace) -> list[dict]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise ValueError("ASCEND preparation needs pyarrow 23.0.1; run with artifacts/eval-data/.venv/bin/python") from exc
    source = args.parquet.resolve(strict=True)
    check_ascend_source(source)
    table = parquet.read_table(source)
    rows = table.to_pylist()
    by_language: dict[str, list[dict]] = {"mixed": [], "zh": [], "en": []}
    for row in rows:
        language = row.get("language")
        duration = float(row.get("duration", 0))
        if language in by_language and duration >= 2.0 and duration > 0:
            by_language[language].append(row)
    for candidates in by_language.values():
        candidates.sort(key=lambda row: (int(row["id"]) if str(row["id"]).isdigit() else str(row["id"])))
    selection_counts = {"mixed": 6, "zh": 3, "en": 3}
    selected = []
    for language, count in selection_counts.items():
        if len(by_language[language]) < count:
            raise ValueError(f"ASCEND has only {len(by_language[language])} {language} clips at least two seconds long")
        selected.extend((language, row) for row in by_language[language][:count])
    selected.sort(key=lambda pair: (int(pair[1]["id"]) if str(pair[1]["id"]).isdigit() else str(pair[1]["id"])))
    output_paths = [args.output_dir / directory / f"ascend-{row['id']}.{extension}" for _, row in selected for directory, extension in (("audio", "wav"), ("references", "txt"))]
    collisions = [path for path in output_paths if path.exists()]
    if collisions:
        raise FileExistsError(f"prepared assets already exist; choose a clean output directory: {collisions[0]}")
    cases = []
    for ordinal, (language, row) in enumerate(selected, 1):
        source_id = str(row["id"])
        audio = row.get("audio") or {}
        audio_bytes = audio.get("bytes")
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise ValueError(f"ASCEND record {source_id} has no embedded audio bytes")
        safe_id = f"ascend-{source_id}"
        audio_rel = Path("audio") / f"{safe_id}.wav"
        ref_rel = Path("references") / f"{safe_id}.txt"
        write_new(args.output_dir / audio_rel, audio_bytes)
        reference_text = str(row["transcription"])
        reference_bytes = (reference_text + "\n").encode("utf-8")
        write_new(args.output_dir / ref_rel, reference_bytes)
        duration = float(row["duration"])
        cases.append(make_case(
            safe_id, "ASCEND", "ascend-test", "heldout", False,
            audio_rel.as_posix(), sha256_bytes(audio_bytes), duration, 0.0, duration,
            None, language, "original", ref_rel.as_posix(), sha256_bytes(reference_bytes), "source-labeled",
            source_id, ASCEND_REVISION, "CC BY-SA 4.0", ASCEND_URL,
            {"dataset": "CAiRE/ASCEND", "dataset_split": "test", "source_audio_path": audio.get("path"),
             "source_language": language, "original_speaker_id": row.get("original_speaker_id"),
             "session_id": row.get("session_id"), "topic": row.get("topic"),
             "sampling": "first six mixed, first three zh, and first three en records with duration >= 2s after sorting source id"},
        ))
    return cases


def silence_case(args: argparse.Namespace) -> dict:
    audio_rel = Path("audio") / "contract-silence-5s.wav"
    ref_rel = Path("references") / "contract-silence-5s.txt"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = args.output_dir / audio_rel
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    with audio_path.open("xb") as stream:
        with wave.open(stream, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(bytes(16000 * 2 * 5))
    ref_bytes = b""
    write_new(args.output_dir / ref_rel, ref_bytes)
    return make_case(
        "contract-silence-5s", "contract-silence", "contract-silence-5s", "dev", False,
        audio_rel.as_posix(), sha256_file(audio_path), 5.0, 0.0, 5.0,
        None, "und", "original", ref_rel.as_posix(), sha256_bytes(ref_bytes), "checked",
        "generated-zero-pcm-v1", "1", "generated fixture", None,
        {"fixture": "mono 16 kHz signed 16-bit PCM with every sample zero", "purpose": "JSON contract and silence hallucination behavior only", "speech_quality_gate_eligible": False},
        {"id": "generator-contract", "method": "deterministic zero-PCM construction", "checked_at": "2026-10-29"},
    )


def starter(args: argparse.Namespace) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.jsonl"
    reserved = [manifest_path, args.output_dir / "audio" / "contract-silence-5s.wav", args.output_dir / "references" / "contract-silence-5s.txt"]
    collision = next((path for path in reserved if path.exists()), None)
    if collision:
        raise FileExistsError(f"prepared asset already exists; choose a clean output directory: {collision}")
    cases = ascend_cases(args)
    cases.append(silence_case(args))
    write_jsonl_new(manifest_path, cases)
    print(json.dumps({"manifest": str(manifest_path), "cases": len(cases), "ascend_cases": len(cases) - 1}, ensure_ascii=False))
    return 0


def parse_clock(value: str) -> float:
    fields = [int(field) for field in value.split(":")]
    if len(fields) == 2:
        return fields[0] * 60 + fields[1]
    if len(fields) == 3:
        return fields[0] * 3600 + fields[1] * 60 + fields[2]
    raise ValueError(f"invalid transcript time: {value}")


BLOCK_RE = re.compile(r"^\s*(\d{2}:\d{2}(?::\d{2})?)\s*(?:\\-|-|–)\s*(\d{2}:\d{2}(?::\d{2})?)\s*(.*?)(?:：|:)\s*(.*)$")


def extract_opening(markdown: str, start_limit: float = 0, end_limit: float = 81) -> tuple[str, list[str], list[str]]:
    marker = "**逐字稿**"
    if marker not in markdown:
        raise ValueError("private draft has no **逐字稿** marker")
    body = markdown.split(marker, 1)[1]
    blocks: list[dict] = []
    current = None
    for line in body.splitlines():
        match = BLOCK_RE.match(line)
        if match:
            current = {"start": parse_clock(match.group(1)), "end": parse_clock(match.group(2)), "speaker": match.group(3).strip(), "text": []}
            blocks.append(current)
            if match.group(4).strip():
                current["text"].append(match.group(4).strip())
        elif current is not None and line.strip():
            current["text"].append(line.strip())
    selected = [block for block in blocks if block["start"] >= start_limit and block["end"] <= end_limit]
    if not selected or selected[0]["start"] != start_limit or selected[-1]["end"] != end_limit:
        raise ValueError("draft does not contain complete transcript blocks spanning exactly 00:00 to 01:21")
    text_parts = ["\n".join(block["text"]).strip() for block in selected]
    speakers = sorted({block["speaker"] for block in selected if block["speaker"]})
    ids = [f"{int(block['start'])}-{int(block['end'])}" for block in selected]
    return "\n".join(part for part in text_parts if part), speakers, ids


def probe_duration(path: Path) -> float:
    completed = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True, capture_output=True, check=True)
    return float(completed.stdout.strip())


def private_draft(args: argparse.Namespace) -> int:
    source = args.source_markdown.resolve(strict=True)
    audio = args.audio.resolve(strict=True)
    output = args.output_dir.resolve()
    text, speakers, block_ids = extract_opening(source.read_text(encoding="utf-8"))
    audio_duration = probe_duration(audio)
    audio_rel = Path("audio") / f"private-opening{audio.suffix.lower()}"
    ref_rel = Path("references") / "private-opening.txt"
    ref_bytes = (text + "\n").encode("utf-8")
    audio_copy = output / audio_rel
    ref_copy = output / ref_rel
    target = output / "manifest.private.jsonl"
    for path in (audio_copy, ref_copy, target):
        if path.exists():
            raise FileExistsError(f"private draft asset already exists; choose a clean output directory: {path}")
    case = make_case(
        "private-interview-opening", "private-interview", "private-interview-recording", "dev", True,
        audio_rel.as_posix(), sha256_file(audio), audio_duration, 0.0, 81.0,
        None, "mixed", "zh-TW", ref_rel.as_posix(), sha256_bytes(ref_bytes), "draft",
        f"{source.name}:00:00-01:21", None, "private draft; local use only", None,
        {"draft_file": source.name, "draft_sha256": sha256_file(source), "audio_file": audio.name, "transcript_section_marker": "**逐字稿**",
         "complete_blocks": block_ids, "printed_speaker_names": speakers,
         "limitation": "draft text agreement only; no speaker or timing metric"},
    )
    starter_manifest = output / "manifest.jsonl"
    cases = []
    if starter_manifest.exists():
        for line in starter_manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append(json.loads(line))
    if any(existing.get("id") == case["id"] for existing in cases):
        raise ValueError(f"case {case['id']} already exists in the starter manifest")
    cases.append(case)
    manifest_payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in cases).encode("utf-8")
    copy_new(audio, audio_copy)
    write_new(ref_copy, ref_bytes)
    write_new(target, manifest_payload)
    print(json.dumps({"manifest": str(target), "case": case["id"], "selected_blocks": block_ids, "speaker_names_recorded_in_provenance": speakers}, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("starter", help="export 12 pinned ASCEND utterances and generated silence fixture")
    prepare.add_argument("--parquet", type=Path, default=Path("artifacts/eval-data/downloads/ascend-test.parquet"))
    prepare.add_argument("--output-dir", type=Path, default=Path("artifacts/eval-data"))
    prepare.set_defaults(func=starter)
    private = commands.add_parser("private-draft", help="import the complete 00:00 to 01:21 transcript blocks after **逐字稿**")
    private.add_argument("--source-markdown", required=True, type=Path)
    private.add_argument("--audio", required=True, type=Path)
    private.add_argument("--output-dir", type=Path, default=Path("artifacts/eval-data"))
    private.set_defaults(func=private_draft)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        print(f"evaluation preparation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
