"""Repeatable CER measurement for controlled transcription hillclimbs."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_PATH = ROOT / "scripts" / "evaluate-suite.py"
SILENCE_CORPUS = "contract-silence"
FAILED_STATUSES = {"failed", "timeout"}


def load_evaluator():
    spec = importlib.util.spec_from_file_location("evaluate_suite", EVALUATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load evaluator: {EVALUATOR_PATH}")
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    return evaluator


EVAL = load_evaluator()


@dataclass(frozen=True)
class ExperimentSpec:
    manifest_sha256: str
    scorer_sha256: str
    harness_sha256: str
    metric_policy: dict
    timeout_seconds: float | None
    repeats: int
    corpus_eligibility: dict


@dataclass
class RepeatResult:
    run_dir: str
    elapsed_seconds: float
    public_ref_chars: int
    edits: int
    failure_ids: list[str]
    public_failure_ids: list[str]
    successful_cases: list[str]
    private_draft_agreement: dict
    silence_insertions: int
    silence_case_ids: list[str]
    failed_silence_ids: list[str]
    source_tree_sha256: str
    corpus_language_slices: dict
    cases: list[dict]

    @property
    def failure_adjusted_cer(self) -> float:
        return self.edits / self.public_ref_chars

    @property
    def completion(self) -> float:
        public_count = len(self.successful_cases) + len(self.public_failure_ids)
        return len(self.successful_cases) / public_count


class MeasurementError(ValueError):
    """A run is incomplete or cannot be trusted as a measurement."""


def manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference_text(case: dict, base: Path) -> str:
    path = EVAL.resolve_member(base, case["reference"]["path"], f"{case['id']}.reference.path")
    return EVAL.normalize_chars(path.read_text(encoding="utf-8"))


def case_category(case: dict) -> str:
    if case["corpus"] == SILENCE_CORPUS:
        return "silence"
    if case["reference"]["status"] == "draft":
        return "private-draft"
    license_text = str(case["provenance"].get("license") or "").casefold()
    if case["reference"]["status"] in {"source-labeled", "checked"} and "private" not in license_text:
        return "public-speech"
    return "excluded"


def corpus_eligibility(cases: list[dict]) -> dict:
    categories = {category: [] for category in ("public-speech", "private-draft", "silence", "excluded")}
    for case in cases:
        categories[case_category(case)].append(case["id"])
    return {
        "rules": {
            "public_speech": "source-labeled or checked reference, non-private license, and corpus is not contract-silence",
            "private_draft": "draft reference, advisory only",
            "silence_guard": "corpus is contract-silence; inserted characters are excluded from speech CER",
        },
        "case_ids": categories,
        "source_labeled_accuracy_claim": False,
    }


def metric_policy() -> dict:
    return {
        "normalization": "NFKC, casefold, keep Unicode letters and numbers",
        "edits": "evaluate-suite edit_counts on normalized Unicode characters",
        "aggregate": "pooled edits divided by pooled public speech reference characters",
        "failed_case": "empty hypothesis; every normalized reference character is a deletion",
        "private_draft": "report agreement separately as advisory; exclude from public speech CER",
        "source_labeled": "source-reference agreement only; no release accuracy claim",
        "silence": "report contract-silence insertions as a zero-character hard guard; exclude from speech CER",
    }


def make_spec(manifest: Path, cases: list[dict], timeout: float | None, repeats: int) -> ExperimentSpec:
    return ExperimentSpec(manifest_digest(manifest), source_digest(EVALUATOR_PATH), source_digest(Path(__file__).resolve()), metric_policy(), timeout, repeats, corpus_eligibility(cases))


def validate_record(case: dict, base: Path, outputs: Path, output_path: Path, sidecar: object) -> dict:
    if not isinstance(sidecar, dict) or type(sidecar.get("schemaVersion")) is not int or sidecar.get("schemaVersion") != EVAL.SCHEMA_VERSION:
        raise MeasurementError(f"{case['id']}: malformed run sidecar")
    if sidecar.get("case_id") != case["id"] or sidecar.get("case_sha256") != EVAL.canonical_digest(case) or sidecar.get("audio_sha256") != case["audio"]["sha256"]:
        raise MeasurementError(f"{case['id']}: sidecar does not match manifest case or audio")
    status = sidecar.get("status")
    if status not in FAILED_STATUSES | {"success"}:
        raise MeasurementError(f"{case['id']}: unknown sidecar status {status!r}")
    elapsed = sidecar.get("elapsed_seconds")
    selected = sidecar.get("selected_audio_seconds")
    expected = case["selection"]["end"] - case["selection"]["start"]
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        raise MeasurementError(f"{case['id']}: sidecar elapsed_seconds is invalid")
    if isinstance(selected, bool) or not isinstance(selected, (int, float)) or abs(selected - expected) > 1e-6:
        raise MeasurementError(f"{case['id']}: sidecar selected duration does not match manifest")
    argv = sidecar.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise MeasurementError(f"{case['id']}: sidecar CLI arguments are missing")
    expected_argv = EVAL.cli_argv(case, base, output_path, argv[0])
    if argv[1:] != expected_argv[1:]:
        raise MeasurementError(f"{case['id']}: recorded CLI arguments do not match manifest conditions")
    if not isinstance(sidecar.get("revision"), dict):
        raise MeasurementError(f"{case['id']}: sidecar source revision identity is missing")
    if status == "success":
        if not output_path.is_file():
            raise MeasurementError(f"{case['id']}: success sidecar has no transcript output")
        try:
            transcript = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MeasurementError(f"{case['id']}: invalid transcript JSON: {exc}") from exc
        try:
            transcript = EVAL.validate_transcript(transcript, case, base)
            bound = EVAL.load_sidecar(case, base, outputs, output_path, transcript)
        except EVAL.SuiteError as exc:
            raise MeasurementError(str(exc)) from exc
        if bound is None:
            raise MeasurementError(f"{case['id']}: successful transcript is not bound to a run sidecar")
        return {"sidecar": bound, "transcript": transcript}
    return {"sidecar": sidecar, "transcript": None}


def read_repeat(run_dir: Path, cases: list[dict], base: Path, references: dict[str, str], elapsed_seconds: float | None = None) -> RepeatResult:
    outputs = run_dir.resolve(strict=True)
    expected_sidecars = {f"{case['id']}.run.json" for case in cases}
    extra = {path.name for path in outputs.glob("*.run.json")} - expected_sidecars
    if extra:
        raise MeasurementError(f"{outputs}: unexpected sidecar(s): {', '.join(sorted(extra))}")
    public_chars = edits = silence_insertions = 0
    silence_cases = []
    failed_silence = []
    source_trees = set()
    successful_public: list[str] = []
    failures: list[str] = []
    public_failures: list[str] = []
    draft_chars = draft_edits = draft_cases = draft_successes = 0
    slices: dict[str, dict] = {}
    records = []
    for case in cases:
        output_path, sidecar_path, _ = EVAL.case_paths(outputs, case["id"])
        if not sidecar_path.is_file():
            raise MeasurementError(f"{case['id']}: missing expected run sidecar")
        try:
            raw_sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MeasurementError(f"{case['id']}: invalid run sidecar: {exc}") from exc
        checked = validate_record(case, base, outputs, output_path, raw_sidecar)
        sidecar = checked["sidecar"]
        source_tree = sidecar["revision"].get("source_tree_sha256")
        if not isinstance(source_tree, str) or not source_tree:
            raise MeasurementError(f"{case['id']}: sidecar source tree identity is missing")
        source_trees.add(source_tree)
        category = case_category(case)
        success = sidecar["status"] == "success"
        score = EVAL.score_case(case, base, checked["transcript"], sidecar) if success else None
        reference = references[case["id"]]
        cer = score["metrics"]["cer"] if score else EVAL.edit_counts(reference, "")
        if not success:
            failures.append(case["id"])
        if category == "public-speech":
            if not reference:
                raise MeasurementError(f"{case['id']}: eligible public speech reference has no normalized characters")
            public_chars += len(reference)
            edits += cer["edits"]
            if success:
                successful_public.append(case["id"])
            else:
                public_failures.append(case["id"])
            key = "|".join((case["corpus"], case["reference_language"], case["reference"]["status"]))
            group = slices.setdefault(key, {"corpus": case["corpus"], "reference_language": case["reference_language"], "reference_status": case["reference"]["status"], "cases": 0, "successful_cases": 0, "reference_chars": 0, "edits": 0})
            group["cases"] += 1
            group["successful_cases"] += int(success)
            group["reference_chars"] += len(reference)
            group["edits"] += cer["edits"]
        elif category == "private-draft":
            draft_chars += len(reference)
            draft_edits += cer["edits"]
            draft_cases += 1
            draft_successes += int(success)
        elif category == "silence":
            silence_cases.append(case["id"])
            if not success:
                failed_silence.append(case["id"])
            silence_insertions += cer["insertions"] if success else 0
        records.append({
            "id": case["id"], "category": category, "reference_status": case["reference"]["status"],
            "run_status": sidecar["status"], "success": success, "error": sidecar.get("error"),
            "cer": cer, "runtime": {"elapsed_seconds": sidecar["elapsed_seconds"], "selected_audio_seconds": sidecar["selected_audio_seconds"]},
            "models": sidecar.get("models"), "revision": sidecar.get("revision"),
        })
    if public_chars == 0 or not successful_public + public_failures:
        raise MeasurementError("manifest has no eligible public speech cases")
    if len(source_trees) != 1:
        raise MeasurementError(f"{outputs}: run records mix source trees")
    for group in slices.values():
        group["failure_adjusted_cer"] = group["edits"] / group["reference_chars"]
        group["completion"] = group["successful_cases"] / group["cases"]
    draft_agreement = {
        "cases": draft_cases, "successful_cases": draft_successes, "reference_chars": draft_chars,
        "edits": draft_edits, "cer": draft_edits / draft_chars if draft_chars else None,
    }
    return RepeatResult(
        str(outputs), elapsed_seconds if elapsed_seconds is not None else sum(row["runtime"]["elapsed_seconds"] for row in records),
        public_chars, edits, failures, public_failures, successful_public, draft_agreement,
        silence_insertions, silence_cases, failed_silence, source_trees.pop(), dict(sorted(slices.items())), records,
    )


def median(values: list[float | int]) -> float | None:
    return statistics.median(values) if values else None


def build_summary(spec: ExperimentSpec, repeats: list[RepeatResult]) -> dict:
    slice_keys = sorted({key for result in repeats for key in result.corpus_language_slices})
    slices = {}
    for key in slice_keys:
        rows = [result.corpus_language_slices[key] for result in repeats if key in result.corpus_language_slices]
        slices[key] = {
            "corpus": rows[0]["corpus"], "reference_language": rows[0]["reference_language"],
            "reference_status": rows[0]["reference_status"],
            "median_failure_adjusted_cer": median([row["failure_adjusted_cer"] for row in rows]),
            "median_completion": median([row["completion"] for row in rows]),
            "reference_chars": rows[0]["reference_chars"], "cases": rows[0]["cases"],
        }
    draft_cers = [row.private_draft_agreement["cer"] for row in repeats if row.private_draft_agreement["cer"] is not None]
    repeat_rows = []
    for result in repeats:
        row = asdict(result)
        row["failure_adjusted_cer"] = result.failure_adjusted_cer
        row["completion"] = result.completion
        repeat_rows.append(row)
    silence_applicable = any(row.silence_case_ids for row in repeats)
    return {
        "schema_version": 1,
        "experiment_spec": asdict(spec),
        "objective": {
            "metric": "failure_adjusted_cer",
            "median_failure_adjusted_cer": median([row.failure_adjusted_cer for row in repeats]),
            "median_completion": median([row.completion for row in repeats]),
            "failure_adjusted_cer_by_repeat": [row.failure_adjusted_cer for row in repeats],
            "completion_by_repeat": [row.completion for row in repeats],
            "public_reference_chars": repeats[0].public_ref_chars,
            "public_case_count": len(repeats[0].successful_cases) + len(repeats[0].public_failure_ids),
            "failure_ids_by_repeat": [row.public_failure_ids for row in repeats],
            "corpus_language_slices": slices,
        },
        "private_draft_advisory": {"median_cer": median(draft_cers), "by_repeat": [row.private_draft_agreement for row in repeats]},
        "silence_guard": {
            "insertions_by_repeat": [row.silence_insertions for row in repeats],
            "case_ids_by_repeat": [row.silence_case_ids for row in repeats],
            "median_insertions": median([row.silence_insertions for row in repeats]),
            "failed_ids_by_repeat": [row.failed_silence_ids for row in repeats],
            "passed": all(row.silence_insertions == 0 and not row.failed_silence_ids for row in repeats) if silence_applicable else None,
        },
        "repeats": repeat_rows,
    }


def read_spec_metadata(run_dirs: list[Path], manifest: Path, cases: list[dict], timeout: float | None) -> tuple[float | None, int, list[int | None], list[float | None]]:
    metadata_paths = [path / "hillclimb-experiment.json" for path in run_dirs]
    present = [path.is_file() for path in metadata_paths]
    if any(present) and not all(present):
        raise MeasurementError("run directories mix frozen and unrecorded experiment metadata")
    if not any(present):
        return timeout, len(run_dirs), [None] * len(run_dirs), [None] * len(run_dirs)
    metadata = []
    for path in metadata_paths:
        try:
            metadata.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise MeasurementError(f"{path}: invalid frozen experiment metadata: {exc}") from exc
    specs = [row.get("spec") for row in metadata]
    expected_fixed = {
        "manifest_sha256": manifest_digest(manifest), "scorer_sha256": source_digest(EVALUATOR_PATH),
        "harness_sha256": source_digest(Path(__file__).resolve()), "metric_policy": metric_policy(),
        "corpus_eligibility": corpus_eligibility(cases),
    }
    for spec in specs:
        if not isinstance(spec, dict) or any(spec.get(key) != value for key, value in expected_fixed.items()):
            raise MeasurementError("run metadata does not match the current manifest or metric policy")
    first = specs[0]
    if any(spec != first for spec in specs[1:]):
        raise MeasurementError("run directories have different frozen experiment specs")
    if len(run_dirs) != first.get("repeats"):
        raise MeasurementError(f"frozen experiment expected {first.get('repeats')} run(s), got {len(run_dirs)}")
    if timeout is not None and timeout != first.get("timeout_seconds"):
        raise MeasurementError("requested timeout differs from the frozen experiment spec")
    indices = [row.get("repeat_index") for row in metadata]
    if sorted(indices) != list(range(1, len(run_dirs) + 1)):
        raise MeasurementError("run metadata repeat indices are missing, duplicated, or incomplete")
    elapsed = [row.get("elapsed_seconds") for row in metadata]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in elapsed):
        raise MeasurementError("run metadata elapsed_seconds is invalid")
    return first.get("timeout_seconds"), first["repeats"], indices, elapsed


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def summarize_runs(manifest: Path, run_dirs: list[Path], out_file: Path, timeout: float | None = None) -> tuple[dict, int]:
    cases, base = EVAL.read_manifest(manifest)
    if not run_dirs:
        raise EVAL.SuiteError("at least one --runs directory is required")
    resolved = [path.resolve(strict=True) for path in run_dirs]
    if len(set(resolved)) != len(resolved):
        raise EVAL.SuiteError("--runs directories must be unique")
    frozen_timeout, repeats_count, _, elapsed = read_spec_metadata(resolved, manifest, cases, timeout)
    spec = make_spec(manifest, cases, frozen_timeout, repeats_count)
    references = {case["id"]: reference_text(case, base) for case in cases}
    results = [read_repeat(path, cases, base, references, elapsed[index]) for index, path in enumerate(resolved)]
    if len({row.source_tree_sha256 for row in results}) != 1:
        raise MeasurementError("run directories contain different source trees; candidates cannot be compared")
    summary = build_summary(spec, results)
    write_json(out_file, summary)
    failed = any(row.failure_ids for row in results)
    silence_regression = summary["silence_guard"]["passed"] is False
    print(json.dumps({
        "summary": str(out_file), "repeats": len(results),
        "median_failure_adjusted_cer": summary["objective"]["median_failure_adjusted_cer"],
        "median_completion": summary["objective"]["median_completion"],
        "silence_insertions_median": summary["silence_guard"]["median_insertions"],
    }, ensure_ascii=False))
    return summary, int(failed or silence_regression)


def measure(args: argparse.Namespace) -> int:
    cases, _ = EVAL.read_manifest(args.manifest)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise EVAL.SuiteError("--timeout must be a finite number greater than zero")
    if args.repeats < 1:
        raise EVAL.SuiteError("--repeats must be at least one")
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    spec = make_spec(args.manifest, cases, args.timeout, args.repeats)
    run_dirs = []
    metadata = {"spec": asdict(spec)}
    for index in range(1, args.repeats + 1):
        run_dir = out_dir / f"repeat-{index:02d}"
        command = [sys.executable, str(EVALUATOR_PATH), "run", str(args.manifest.resolve()), "--out", str(run_dir), "--timeout", str(args.timeout), "--bun", args.bun]
        started = time.monotonic()
        result = subprocess.run(command, text=True, capture_output=True)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode not in (0, 1):
            raise MeasurementError(f"repeat {index}: evaluator runner exited with status {result.returncode}")
        run_dirs.append(run_dir)
        write_json(run_dir / "hillclimb-experiment.json", {**metadata, "repeat_index": index, "elapsed_seconds": time.monotonic() - started})
    if manifest_digest(args.manifest) != spec.manifest_sha256:
        raise MeasurementError("manifest changed during measurement")
    _, status = summarize_runs(args.manifest, run_dirs, out_dir / "summary.json", args.timeout)
    return status


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("measure", help="run and summarize repeated suite measurements")
    run.add_argument("manifest", type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--timeout", type=float, default=120)
    run.add_argument("--bun", default="bun")
    run.set_defaults(func=measure)
    score = commands.add_parser("summarize", help="rescore completed run directories with this metric policy")
    score.add_argument("manifest", type=Path)
    score.add_argument("--runs", required=True, nargs="+", type=Path)
    score.add_argument("--out", required=True, type=Path)
    score.add_argument("--timeout", type=float)
    score.set_defaults(func=lambda args: summarize_runs(args.manifest, args.runs, args.out, args.timeout)[1])
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except MeasurementError as exc:
        print(f"measurement error: {exc}", file=sys.stderr)
        return 1
    except (EVAL.SuiteError, OSError, ValueError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
