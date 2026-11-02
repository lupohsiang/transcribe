"""Validate, run, and score a local audio evaluation suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
import unicodedata


SCHEMA_VERSION = 1
STATUSES = {"draft", "source-labeled", "checked"}
LANGUAGES = {"zh", "en", "mixed", "und"}
CASE_KEYS = {
    "id", "corpus", "group", "split", "used_for_tuning", "audio",
    "selection", "language_hint", "reference_language", "chinese_script",
    "reference", "provenance",
}


class SuiteError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SuiteError(f"{name} must be a finite number")
    return float(value)


def resolve_member(base: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SuiteError(f"{name} must be a non-empty path")
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve(strict=True)


def read_manifest(path: Path) -> tuple[list[dict], Path]:
    manifest = path.resolve(strict=True)
    cases: list[dict] = []
    for line_number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SuiteError(f"{manifest}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(case, dict):
            raise SuiteError(f"{manifest}:{line_number}: each manifest line must be an object")
        cases.append(case)
    if not cases:
        raise SuiteError("manifest contains no cases")
    validate_cases(cases, manifest.parent)
    return cases, manifest.parent


def validate_cases(cases: list[dict], base: Path) -> None:
    ids: set[str] = set()
    groups: dict[str, str] = {}
    audios: dict[tuple[str, str], str] = {}
    for index, case in enumerate(cases, 1):
        label = f"case {index}"
        if set(case) != CASE_KEYS:
            missing = sorted(CASE_KEYS - set(case))
            extra = sorted(set(case) - CASE_KEYS)
            raise SuiteError(f"{label}: manifest fields mismatch; missing={missing}, extra={extra}")
        case_id = case["id"]
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", case_id):
            raise SuiteError(f"{label}: id must be a safe non-empty filename")
        if case_id in ids:
            raise SuiteError(f"duplicate case id: {case_id}")
        ids.add(case_id)
        for key in ("corpus", "group"):
            if not isinstance(case[key], str) or not case[key].strip():
                raise SuiteError(f"{case_id}: {key} must be a non-empty string")
        if case["split"] not in ("dev", "heldout"):
            raise SuiteError(f"{case_id}: split must be dev or heldout")
        if not isinstance(case["used_for_tuning"], bool):
            raise SuiteError(f"{case_id}: used_for_tuning must be boolean")
        if case["split"] == "heldout" and case["used_for_tuning"]:
            raise SuiteError(f"{case_id}: heldout cases cannot be marked used_for_tuning")
        old_split = groups.setdefault(case["group"], case["split"])
        if old_split != case["split"]:
            raise SuiteError(f"recording group {case['group']} crosses dev and heldout")
        if not isinstance(case["audio"], dict) or set(case["audio"]) != {"path", "sha256", "duration"}:
            raise SuiteError(f"{case_id}: audio must contain path, sha256, and duration")
        audio_path = resolve_member(base, case["audio"]["path"], f"{case_id}.audio.path")
        duration = finite_number(case["audio"]["duration"], f"{case_id}.audio.duration")
        if duration <= 0:
            raise SuiteError(f"{case_id}: audio duration must be positive")
        audio_hash = case["audio"]["sha256"]
        if not isinstance(audio_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", audio_hash):
            raise SuiteError(f"{case_id}: audio sha256 must be lowercase hexadecimal")
        if sha256_file(audio_path) != audio_hash:
            raise SuiteError(f"{case_id}: audio SHA-256 mismatch: {audio_path}")
        for identity in (str(audio_path), audio_hash):
            key = (identity, case["split"])
            other_splits = {split for (known, split) in audios if known == identity and split != case["split"]}
            if other_splits:
                raise SuiteError(f"audio alias crosses dev and heldout: {identity}")
            audios[key] = case["split"]
        if not isinstance(case["selection"], dict) or set(case["selection"]) != {"start", "end"}:
            raise SuiteError(f"{case_id}: selection must contain start and end")
        start = finite_number(case["selection"]["start"], f"{case_id}.selection.start")
        end = finite_number(case["selection"]["end"], f"{case_id}.selection.end")
        if start < 0 or start >= end or end > duration + 1e-6:
            raise SuiteError(f"{case_id}: selection must be ordered within the audio duration")
        if case["language_hint"] is not None and not isinstance(case["language_hint"], str):
            raise SuiteError(f"{case_id}: language_hint must be a string or null")
        if case["chinese_script"] not in ("original", "zh-TW"):
            raise SuiteError(f"{case_id}: chinese_script must be original or zh-TW")
        if not isinstance(case["reference_language"], str) or case["reference_language"] not in LANGUAGES:
            raise SuiteError(f"{case_id}: reference_language must be one of {sorted(LANGUAGES)}")
        reference = case["reference"]
        if not isinstance(reference, dict) or set(reference) - {"path", "sha256", "status", "reviewer"} or not {"path", "sha256", "status"} <= set(reference):
            raise SuiteError(f"{case_id}: reference requires path, sha256, status, and optional reviewer")
        reference_path = resolve_member(base, reference["path"], f"{case_id}.reference.path")
        reference_hash = reference["sha256"]
        if not isinstance(reference_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", reference_hash):
            raise SuiteError(f"{case_id}: reference sha256 must be lowercase hexadecimal")
        if sha256_file(reference_path) != reference_hash:
            raise SuiteError(f"{case_id}: reference SHA-256 mismatch: {reference_path}")
        if not isinstance(reference["status"], str) or reference["status"] not in STATUSES:
            raise SuiteError(f"{case_id}: reference status must be one of {sorted(STATUSES)}")
        reviewer = reference.get("reviewer")
        if reference["status"] == "checked":
            if not isinstance(reviewer, dict) or not isinstance(reviewer.get("id"), str) or not reviewer["id"].strip() or not isinstance(reviewer.get("method"), str) or not reviewer["method"].strip():
                raise SuiteError(f"{case_id}: checked references require reviewer id and method")
        elif reviewer is not None:
            raise SuiteError(f"{case_id}: reviewer metadata is only valid for checked references")
        provenance = case["provenance"]
        expected_provenance = {"source_id", "source_revision", "license", "source_url", "detail"}
        if not isinstance(provenance, dict) or set(provenance) != expected_provenance:
            raise SuiteError(f"{case_id}: provenance must contain {sorted(expected_provenance)}")
        for key in ("source_id", "detail"):
            if key == "source_id" and (not isinstance(provenance[key], str) or not provenance[key].strip()):
                raise SuiteError(f"{case_id}: provenance.source_id must be a non-empty string")
            if key == "detail" and not isinstance(provenance[key], dict):
                raise SuiteError(f"{case_id}: provenance.detail must be an object")
        for key in ("source_revision", "license", "source_url"):
            if provenance[key] is not None and not isinstance(provenance[key], str):
                raise SuiteError(f"{case_id}: provenance.{key} must be a string or null")


def normalize_chars(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold()
        if unicodedata.category(char)[0] in ("L", "N")
    )


def is_han(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x3134F
        or 0x2E80 <= code <= 0x2FDF or 0x3005 <= code <= 0x3007
    )


def mixed_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    word: list[str] = []
    for char in unicodedata.normalize("NFKC", text).casefold():
        if unicodedata.category(char)[0] not in ("L", "N"):
            if word:
                tokens.append("".join(word))
                word.clear()
        elif is_han(char):
            if word:
                tokens.append("".join(word))
                word.clear()
            tokens.append(char)
        else:
            word.append(char)
    if word:
        tokens.append("".join(word))
    return tokens


def english_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    word: list[str] = []
    for char in unicodedata.normalize("NFKC", text).casefold():
        if unicodedata.category(char)[0] in ("L", "N") and not is_han(char):
            word.append(char)
        elif word:
            tokens.append("".join(word))
            word.clear()
    if word:
        tokens.append("".join(word))
    return tokens


def edit_counts(reference: list[str] | str, hypothesis: list[str] | str) -> dict:
    previous = [(column, 0, 0, column) for column in range(len(hypothesis) + 1)]
    for row, expected in enumerate(reference, 1):
        current = [(row, 0, row, 0)]
        for column, actual in enumerate(hypothesis, 1):
            diagonal = previous[column - 1]
            changed = expected != actual
            substitution = (diagonal[0] + changed, diagonal[1] + changed, diagonal[2], diagonal[3])
            above = previous[column]
            deletion = (above[0] + 1, above[1], above[2] + 1, above[3])
            left = current[column - 1]
            insertion = (left[0] + 1, left[1], left[2], left[3] + 1)
            current.append(min((substitution, deletion, insertion), key=lambda item: item[0]))
        previous = current
    edits, substitutions, deletions, insertions = previous[-1]
    denominator = len(reference)
    return {
        "reference_units": denominator,
        "hypothesis_units": len(hypothesis),
        "edits": edits,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "rate": edits / denominator if denominator else None,
    }


def validate_transcript(value: object, case: dict, base: Path) -> dict:
    if not isinstance(value, dict):
        raise SuiteError(f"{case['id']}: transcript must be a JSON object")
    required = {"schemaVersion", "source", "segments", "warnings", "models", "timing"}
    if not required <= set(value) or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise SuiteError(f"{case['id']}: transcript is missing v1 fields or has an unsupported schema")
    source = value["source"]
    if not isinstance(source, dict) or not {"path", "duration", "selection"} <= set(source):
        raise SuiteError(f"{case['id']}: transcript source metadata is incomplete")
    audio_path = resolve_member(base, case["audio"]["path"], f"{case['id']}.audio.path")
    try:
        source_path = Path(source["path"]).resolve(strict=True)
    except (TypeError, OSError) as exc:
        raise SuiteError(f"{case['id']}: transcript source path cannot be resolved") from exc
    if source_path != audio_path:
        raise SuiteError(f"{case['id']}: transcript source path does not match manifest audio")
    source_duration = finite_number(source["duration"], f"{case['id']}.transcript.source.duration")
    if abs(source_duration - case["audio"]["duration"]) > 0.02:
        raise SuiteError(f"{case['id']}: transcript source duration does not match manifest audio")
    selection = source["selection"]
    if not isinstance(selection, dict) or not {"start", "end"} <= set(selection):
        raise SuiteError(f"{case['id']}: transcript selection metadata is incomplete")
    start = finite_number(selection["start"], f"{case['id']}.transcript.selection.start")
    end = finite_number(selection["end"], f"{case['id']}.transcript.selection.end")
    if abs(start - case["selection"]["start"]) > 1e-6 or abs(end - case["selection"]["end"]) > 0.02:
        raise SuiteError(f"{case['id']}: transcript selection does not match manifest audio range")
    if value["timing"] not in ("speaker-turn", "forced-aligned"):
        raise SuiteError(f"{case['id']}: transcript timing mode is invalid")
    models = value["models"]
    model_names = {"asr", "diarization"} | ({"alignment"} if value["timing"] == "forced-aligned" else set())
    if not isinstance(models, dict) or not model_names <= set(models) or any(not isinstance(models[name], str) or not models[name].strip() for name in model_names):
        raise SuiteError(f"{case['id']}: transcript model pins are incomplete")
    if not isinstance(value["warnings"], list) or any(not isinstance(warning, str) for warning in value["warnings"]):
        raise SuiteError(f"{case['id']}: transcript warnings must be strings")
    if not isinstance(value["segments"], list):
        raise SuiteError(f"{case['id']}: transcript segments must be an array")
    previous_end = start
    for number, segment in enumerate(value["segments"], 1):
        name = f"{case['id']}.segments[{number}]"
        if not isinstance(segment, dict) or not {"start", "end", "text", "speaker", "activeSpeakers"} <= set(segment):
            raise SuiteError(f"{name}: segment fields are incomplete")
        seg_start = finite_number(segment["start"], f"{name}.start")
        seg_end = finite_number(segment["end"], f"{name}.end")
        if seg_start >= seg_end or seg_start < start or seg_end > end + 1e-6 or seg_start < previous_end:
            raise SuiteError(f"{name}: segment bounds must be ordered within the selected source range")
        if not isinstance(segment["text"], str) or not isinstance(segment["activeSpeakers"], list) or any(not isinstance(speaker, str) for speaker in segment["activeSpeakers"]):
            raise SuiteError(f"{name}: text and activeSpeakers have invalid types")
        active = segment["activeSpeakers"]
        if len(active) != len(set(active)) or segment["speaker"] != (active[0] if len(active) == 1 else None):
            raise SuiteError(f"{name}: speaker must match the activeSpeakers set")
        if segment["speaker"] is not None and not isinstance(segment["speaker"], str):
            raise SuiteError(f"{name}: speaker must be a string or null")
        previous_end = seg_end
    return value


def output_hypothesis(transcript: dict) -> str:
    return " ".join(segment["text"] for segment in transcript["segments"])


def score_case(case: dict, base: Path, transcript: dict, sidecar: dict | None) -> dict:
    reference_path = resolve_member(base, case["reference"]["path"], f"{case['id']}.reference.path")
    reference_text = reference_path.read_text(encoding="utf-8")
    hypothesis = output_hypothesis(transcript)
    reference_chars = normalize_chars(reference_text)
    hypothesis_chars = normalize_chars(hypothesis)
    result = {
        "id": case["id"],
        "corpus": case["corpus"],
        "group": case["group"],
        "split": case["split"],
        "used_for_tuning": case["used_for_tuning"],
        "reference_status": case["reference"]["status"],
        "reference_language": case["reference_language"],
        "run_status": "saved-output" if sidecar is None else sidecar.get("status", "unknown"),
        "sidecar_bound": sidecar is not None,
        "runtime": {
            "elapsed_seconds": sidecar.get("elapsed_seconds") if sidecar else None,
            "selected_audio_seconds": sidecar.get("selected_audio_seconds") if sidecar else None,
            "realtime_factor": (sidecar["elapsed_seconds"] / sidecar["selected_audio_seconds"]) if sidecar and sidecar["selected_audio_seconds"] else None,
        },
        "metrics": {
            "cer": edit_counts(reference_chars, hypothesis_chars),
            "word_error_rate": edit_counts(english_tokens(reference_text), english_tokens(hypothesis)) if case["reference_language"] == "en" else None,
            "mixed_error_rate": edit_counts(mixed_tokens(reference_text), mixed_tokens(hypothesis)) if case["reference_language"] == "mixed" else None,
            "hallucination_events": sum(bool(normalize_chars(segment["text"])) for segment in transcript["segments"]) if not reference_chars else None,
            "hallucinated_characters": len(hypothesis_chars) if not reference_chars else None,
            "unknown_speaker_segments": sum(segment["speaker"] is None for segment in transcript["segments"]),
            "overlap_segments": sum(len(segment["activeSpeakers"]) > 1 for segment in transcript["segments"]),
        },
    }
    result["warnings"] = transcript["warnings"]
    result["models"] = transcript["models"]
    return result


def case_paths(out_dir: Path, case_id: str) -> tuple[Path, Path, Path]:
    return out_dir / f"{case_id}.json", out_dir / f"{case_id}.run.json", out_dir / f"{case_id}.stderr.log"


def revision_identity(root: Path) -> dict:
    def git_value(*command: str) -> str | None:
        result = subprocess.run(["git", *command], cwd=root, text=True, capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else None

    paths = [*root.joinpath("src").rglob("*"), *root.joinpath("python").rglob("*.py"), *root.joinpath("scripts").glob("*.py")]
    paths.extend(root / name for name in ("package.json", "bun.lock", "pyproject.toml", "uv.lock", "model-pins.json") if (root / name).is_file())
    entries = {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(paths) if path.is_file()}
    tree_hash = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    diff = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--", "src", "python", "scripts", "package.json", "bun.lock", "pyproject.toml", "uv.lock", "model-pins.json"], cwd=root, capture_output=True)
    return {
        "git_head": git_value("rev-parse", "HEAD"),
        "dirty_diff_sha256": hashlib.sha256(diff.stdout).hexdigest() if diff.returncode == 0 else None,
        "source_tree_sha256": tree_hash,
        "source_files_sha256": entries,
    }


def cli_argv(case: dict, base: Path, output_path: Path, bun: str) -> list[str]:
    audio_path = resolve_member(base, case["audio"]["path"], f"{case['id']}.audio.path")
    argv = [bun, "src/cli.ts", "run", str(audio_path), "--start", str(case["selection"]["start"]), "--duration", str(case["selection"]["end"] - case["selection"]["start"]), "--format", "json", "--output", str(output_path), "--chinese-script", case["chinese_script"], "--quiet"]
    if case["language_hint"]:
        argv.extend(["--language", case["language_hint"]])
    return argv


def stop_process_group(process: subprocess.Popen, initial_signal: int = signal.SIGINT) -> tuple[str, str, list[str]]:
    sent = []
    for signal_number, wait_seconds in ((initial_signal, 2), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        try:
            os.killpg(process.pid, signal_number)
            sent.append(signal.Signals(signal_number).name)
        except (ProcessLookupError, PermissionError):
            try:
                process.send_signal(signal_number)
                sent.append(signal.Signals(signal_number).name)
            except ProcessLookupError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=wait_seconds)
            return stdout or "", stderr or "", sent
        except subprocess.TimeoutExpired:
            continue
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()
    return "", "timed-out process pipes closed after forced cleanup", sent


def run_suite(args: argparse.Namespace) -> int:
    cases, base = read_manifest(args.manifest)
    chosen = [case for case in cases if args.case is None or case["id"] == args.case]
    if not chosen:
        raise SuiteError(f"no manifest case matches --case {args.case!r}")
    if args.split:
        chosen = [case for case in chosen if case["split"] == args.split]
    if not chosen:
        raise SuiteError("no cases remain after split filtering")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise SuiteError("--timeout must be a finite number greater than zero")
    out_dir = args.out.resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise SuiteError(f"output directory already exists; choose a new path: {out_dir}") from exc
    failures = 0
    root = Path(__file__).resolve().parents[1]
    revision = revision_identity(root)
    for case in chosen:
        output_path, sidecar_path, log_path = case_paths(out_dir, case["id"])
        argv = cli_argv(case, base, output_path, args.bun)
        status = "success"
        details = None
        stderr = ""
        process = None
        started = time.monotonic()
        try:
            interrupt_pending = False

            def defer_startup_interrupt(_signum, _frame):
                nonlocal interrupt_pending
                interrupt_pending = True

            previous_sigint = signal.signal(signal.SIGINT, defer_startup_interrupt)
            try:
                process = subprocess.Popen(argv, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
            if interrupt_pending:
                raise KeyboardInterrupt
            try:
                stdout, stderr = process.communicate(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                status = "timeout"
                stdout, stderr, signals_sent = stop_process_group(process)
                details = f"CLI exceeded {args.timeout:g} seconds; process-group cleanup sent {', '.join(signals_sent)}"
            except KeyboardInterrupt:
                status = "interrupted"
                stdout, stderr, signals_sent = stop_process_group(process)
                details = f"interrupted by user; process-group cleanup sent {', '.join(signals_sent)}"
            if stdout:
                stderr += "\nstdout:\n" + stdout
            if status in ("timeout", "interrupted"):
                pass
            elif process.returncode != 0:
                status = "failed"
                details = f"CLI exited with status {process.returncode}"
            elif not output_path.is_file():
                status = "failed"
                details = "CLI completed without writing transcript JSON"
            else:
                transcript = json.loads(output_path.read_text(encoding="utf-8"))
                validate_transcript(transcript, case, base)
        except subprocess.TimeoutExpired as exc:
            status = "timeout"
            details = f"CLI exceeded {args.timeout:g} seconds"
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        except (OSError, json.JSONDecodeError, SuiteError) as exc:
            status = "failed"
            details = str(exc)
        except KeyboardInterrupt:
            if process is None:
                raise
            status = "interrupted"
            stdout, stderr, signals_sent = stop_process_group(process)
            details = f"interrupted during CLI startup; process-group cleanup sent {', '.join(signals_sent)}"
        elapsed_seconds = time.monotonic() - started
        log_path.write_text(stderr, encoding="utf-8")
        transcript_value = None
        output_hash = None
        if output_path.is_file():
            try:
                transcript_value = json.loads(output_path.read_text(encoding="utf-8"))
                output_hash = sha256_file(output_path)
            except (OSError, json.JSONDecodeError):
                pass
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "case_id": case["id"],
            "case_sha256": canonical_digest(case),
            "audio_sha256": case["audio"]["sha256"],
            "output_sha256": output_hash,
            "argv": argv,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "selected_audio_seconds": case["selection"]["end"] - case["selection"]["start"],
            "revision": revision,
            "status": status,
            "error": details,
            "models": transcript_value.get("models") if isinstance(transcript_value, dict) else None,
            "warnings": transcript_value.get("warnings") if isinstance(transcript_value, dict) else None,
            "stderr_log": log_path.name,
        }
        sidecar_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{case['id']}: {status}" + (f" ({details})" if details else ""), file=sys.stderr)
        failures += status != "success"
        if status == "interrupted":
            return 130
    if failures:
        print(f"{failures} of {len(chosen)} case(s) failed; partial runs cannot be scored as complete", file=sys.stderr)
        return 1
    return 0


def load_sidecar(case: dict, base: Path, outputs: Path, transcript_path: Path, transcript: dict) -> dict | None:
    _, sidecar_path, _ = case_paths(outputs, case["id"])
    if not sidecar_path.exists():
        return None
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"{case['id']}: invalid run sidecar: {exc}") from exc
    if not isinstance(sidecar, dict) or sidecar.get("schemaVersion") != SCHEMA_VERSION or sidecar.get("case_id") != case["id"]:
        raise SuiteError(f"{case['id']}: run sidecar is malformed or identifies another case")
    if sidecar.get("case_sha256") != canonical_digest(case) or sidecar.get("audio_sha256") != case["audio"]["sha256"]:
        raise SuiteError(f"{case['id']}: run sidecar does not match manifest case or audio")
    if sidecar.get("output_sha256") != sha256_file(transcript_path):
        raise SuiteError(f"{case['id']}: transcript output hash does not match run sidecar")
    if sidecar.get("status") != "success":
        raise SuiteError(f"{case['id']}: run sidecar status is {sidecar.get('status')!r}")
    argv = sidecar.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise SuiteError(f"{case['id']}: run sidecar is missing its CLI arguments")
    if argv[1:] != cli_argv(case, base, transcript_path, argv[0])[1:]:
        raise SuiteError(f"{case['id']}: recorded CLI arguments do not match manifest conditions")
    if sidecar.get("models") != transcript.get("models"):
        raise SuiteError(f"{case['id']}: recorded model pins do not match transcript JSON")
    elapsed = sidecar.get("elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        raise SuiteError(f"{case['id']}: sidecar elapsed_seconds is invalid")
    expected_duration = case["selection"]["end"] - case["selection"]["start"]
    selected_duration = sidecar.get("selected_audio_seconds")
    if isinstance(selected_duration, bool) or not isinstance(selected_duration, (int, float)) or abs(selected_duration - expected_duration) > 1e-6:
        raise SuiteError(f"{case['id']}: sidecar selected audio duration does not match manifest")
    if not isinstance(sidecar.get("revision"), dict):
        raise SuiteError(f"{case['id']}: sidecar is missing source revision identity")
    return sidecar


def metric_aggregate(results: list[dict], metric_name: str) -> dict:
    picked = [result for result in results if result["metrics"].get(metric_name) is not None]
    rows = [result["metrics"][metric_name] for result in picked]
    total_units = sum(row["reference_units"] for row in rows)
    total_edits = sum(row["edits"] for row in rows)
    per_group: dict[str, list[float]] = {}
    for result, row in zip(picked, rows):
        if row["rate"] is not None:
            per_group.setdefault(result["group"], []).append(row["rate"])
    macro = {group: sum(rates) / len(rates) for group, rates in sorted(per_group.items())}
    return {
        "cases": len(rows),
        "reference_units": total_units,
        "edits": total_edits,
        "substitutions": sum(row["substitutions"] for row in rows),
        "deletions": sum(row["deletions"] for row in rows),
        "insertions": sum(row["insertions"] for row in rows),
        "pooled_rate": total_edits / total_units if total_units else None,
        "recording_macro_rate": sum(macro.values()) / len(macro) if macro else None,
        "recording_macro_rates": macro,
    }


def runtime_aggregate(results: list[dict]) -> dict:
    runtimes = [row["runtime"] for row in results if row["runtime"]["elapsed_seconds"] is not None]
    factors = [row["realtime_factor"] for row in runtimes if row["realtime_factor"] is not None]
    return {
        "cases": len(runtimes),
        "total_elapsed_seconds": sum(row["elapsed_seconds"] for row in runtimes),
        "mean_realtime_factor": sum(factors) / len(factors) if factors else None,
    }


def score_suite(args: argparse.Namespace) -> int:
    cases, base = read_manifest(args.manifest)
    cases = [case for case in cases if (args.split is None or case["split"] == args.split) and (args.case is None or case["id"] == args.case)]
    if not cases:
        raise SuiteError("no cases remain after score filters")
    outputs = args.outputs.resolve(strict=True)
    results = []
    missing = []
    for case in cases:
        transcript_path, _, _ = case_paths(outputs, case["id"])
        if not transcript_path.is_file():
            missing.append(case["id"])
            results.append({"id": case["id"], "corpus": case["corpus"], "group": case["group"], "split": case["split"], "used_for_tuning": case["used_for_tuning"], "reference_status": case["reference"]["status"], "reference_language": case["reference_language"], "run_status": "missing-output", "sidecar_bound": False, "metrics": None})
            continue
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SuiteError(f"{case['id']}: invalid transcript JSON: {exc}") from exc
        transcript = validate_transcript(transcript, case, base)
        sidecar = load_sidecar(case, base, outputs, transcript_path, transcript)
        results.append(score_case(case, base, transcript, sidecar))
    buckets: dict[str, list[dict]] = {}
    for result in results:
        if result["metrics"] is None:
            continue
        key = "|".join((result["corpus"], result["split"], result["reference_status"], result["reference_language"]))
        buckets.setdefault(key, []).append(result)
    aggregates = {}
    for key, rows in sorted(buckets.items()):
        aggregates[key] = {
            metric: metric_aggregate(rows, metric)
            for metric in ("cer", "word_error_rate", "mixed_error_rate")
            if any(row["metrics"].get(metric) is not None for row in rows)
        }
    eligible = [result for result in results if result["metrics"] is not None and result["sidecar_bound"] and result["reference_status"] == "checked" and result["split"] == "heldout" and not result["used_for_tuning"] and result["corpus"] != "contract-silence" and result["metrics"]["cer"]["reference_units"] > 0]
    gate = {
        "eligible_checked_speech_cases": [result["id"] for result in eligible],
        "thresholds_configured": False,
        "ready": False,
        "reason": "no checked-reference thresholds are configured",
    }
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "metric_policy": {
            "character_normalization": "NFKC, casefold, keep Unicode letters and numbers",
            "mixed_tokens": "each Han character plus contiguous non-Han letter/number runs; segment boundaries separated by spaces",
            "word_error_rate": "English-only cases; whitespace and punctuation delimit word tokens",
            "overlap_and_unknown_speaker_counts": "diagnostic only; no diarization accuracy claim",
            "source_labels": "source-reference agreement, not human-checked accuracy",
            "draft_labels": "provisional reference agreement",
            "empty_reference": "CER undefined; report inserted characters and nonempty segment events",
        },
        "case_count": len(cases),
        "scored_case_count": len(results) - len(missing),
        "missing_outputs": missing,
        "cases": results,
        "aggregates_by_corpus_split_reference_status": aggregates,
        "runtime_by_corpus_split_reference_status": {
            key: runtime_aggregate(rows) for key, rows in sorted(buckets.items())
        },
        "release_gate": gate,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.report), "scored": report["scored_case_count"], "missing": len(missing), "release_gate_ready": False}, ensure_ascii=False))
    if missing:
        print(f"missing transcript outputs for: {', '.join(missing)}", file=sys.stderr)
        return 1
    if args.require_checked and not eligible:
        print("no checked, heldout speech references with bound successful run records qualify for the release gate", file=sys.stderr)
        return 1
    return 0


def validate_command(args: argparse.Namespace) -> int:
    cases, _ = read_manifest(args.manifest)
    print(json.dumps({"valid": True, "cases": len(cases), "ids": [case["id"] for case in cases]}, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate manifest structure, hashes, and splits")
    validate.add_argument("manifest", type=Path)
    validate.set_defaults(func=validate_command)
    run = commands.add_parser("run", help="run the production CLI for each selected case")
    run.add_argument("manifest", type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--split", choices=("dev", "heldout"))
    run.add_argument("--case")
    run.add_argument("--bun", default="bun", help="Bun executable; override for a deterministic CLI boundary fixture")
    run.add_argument("--timeout", type=float, default=21600)
    run.set_defaults(func=run_suite)
    score = commands.add_parser("score", help="score previously saved transcript JSON")
    score.add_argument("manifest", type=Path)
    score.add_argument("--outputs", required=True, type=Path)
    score.add_argument("--report", required=True, type=Path)
    score.add_argument("--split", choices=("dev", "heldout"))
    score.add_argument("--case")
    score.add_argument("--require-checked", action="store_true")
    score.set_defaults(func=score_suite)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (SuiteError, OSError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
