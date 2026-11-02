import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hillclimb-eval.py"


class HillclimbEvalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = self.root / "manifest.jsonl"
        self.cases = self.make_cases()
        self.write_manifest(self.cases)

    def tearDown(self):
        self.temp.cleanup()

    def make_cases(self, include_failure=True):
        rows = []
        definitions = [
            ("public-a", "ASCEND", "source-labeled", "abc", "en", "CC BY-SA 4.0"),
            ("public-b", "ASCEND", "checked", "de", "en", "CC BY-SA 4.0"),
            ("private-draft", "private-interview", "draft", "secret", "mixed", "private draft; local use only"),
            ("contract-silence-5s", "contract-silence", "checked", "", "und", "generated fixture"),
        ]
        for case_id, corpus, status, text, language, license_name in definitions:
            audio = self.root / f"{case_id}.wav"
            audio.write_bytes(f"audio:{case_id}".encode())
            reference = self.root / f"{case_id}.txt"
            reference.write_text(text, encoding="utf-8")
            reviewer = {"id": "reviewer", "method": "listened"} if status == "checked" else None
            rows.append({
                "id": case_id, "corpus": corpus, "group": case_id, "split": "heldout", "used_for_tuning": False,
                "audio": {"path": str(audio), "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(), "duration": 10.0},
                "selection": {"start": 0.0, "end": 5.0}, "language_hint": None,
                "reference_language": language, "chinese_script": "original",
                "reference": {"path": str(reference), "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(), "status": status, **({"reviewer": reviewer} if reviewer else {})},
                "provenance": {"source_id": case_id, "source_revision": "fixture-v1", "license": license_name, "source_url": None, "detail": {}},
            })
        return rows if include_failure else [row for row in rows if row["id"] != "public-b"]

    def write_manifest(self, cases):
        self.manifest.write_text("".join(json.dumps(row) + "\n" for row in cases), encoding="utf-8")

    def fake_bun(self, silence_text="", failure_ids=("public-b",)):
        script = self.root / "fake-bun"
        contents = """#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
audio = pathlib.Path(args[2]).resolve()
out = pathlib.Path(args[args.index('--output') + 1])
case_id = out.stem
if case_id in FAILED_CASES:
    raise SystemExit(7)
text = {'public-a': 'axc', 'private-draft': 'x', 'contract-silence-5s': SILENCE_TEXT}[case_id]
start = float(args[args.index('--start') + 1])
duration = float(args[args.index('--duration') + 1])
value = {'schemaVersion': 1, 'source': {'path': str(audio), 'duration': 10.0, 'selection': {'start': start, 'end': start + duration}}, 'segments': [], 'warnings': [], 'timing': 'forced-aligned', 'models': {'asr': 'asr@fixture', 'diarization': 'diarization@fixture', 'alignment': 'alignment@fixture'}}
if text:
    value['segments'] = [{'start': start + 0.1, 'end': start + 1.0, 'text': text, 'speaker': 'speaker_0', 'activeSpeakers': ['speaker_0']}]
out.write_text(json.dumps(value), encoding='utf-8')
""".replace("SILENCE_TEXT", repr(silence_text)).replace("FAILED_CASES", repr(failure_ids))
        script.write_text(contents, encoding="utf-8")
        script.chmod(0o755)
        return script

    def command(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], text=True, capture_output=True, timeout=30)

    def measure(self, output, repeats=1, cases=None, silence_text="", failure_ids=("public-b",)):
        if cases is not None:
            self.write_manifest(cases)
        return self.command("measure", self.manifest, "--out", output, "--repeats", repeats, "--timeout", 5, "--bun", self.fake_bun(silence_text, failure_ids))

    def test_failure_adjusted_cer_medians_and_private_draft_are_separate(self):
        output = self.root / "measure"
        result = self.measure(output, repeats=2)
        self.assertEqual(result.returncode, 1, result.stderr)
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["experiment_spec"]["repeats"], 2)
        self.assertEqual(summary["experiment_spec"]["timeout_seconds"], 5.0)
        self.assertEqual(len(summary["experiment_spec"]["harness_sha256"]), 64)
        self.assertEqual(len(summary["experiment_spec"]["scorer_sha256"]), 64)
        self.assertEqual(summary["experiment_spec"]["corpus_eligibility"]["case_ids"]["public-speech"], ["public-a", "public-b"])
        self.assertEqual(summary["objective"]["public_reference_chars"], 5)
        self.assertEqual(summary["objective"]["median_failure_adjusted_cer"], 3 / 5)
        self.assertEqual(summary["objective"]["median_completion"], 1 / 2)
        self.assertEqual(summary["objective"]["failure_adjusted_cer_by_repeat"], [3 / 5, 3 / 5])
        self.assertEqual(summary["objective"]["completion_by_repeat"], [1 / 2, 1 / 2])
        self.assertEqual(summary["objective"]["failure_ids_by_repeat"], [["public-b"], ["public-b"]])
        self.assertEqual(summary["private_draft_advisory"]["median_cer"], 1.0)
        self.assertTrue(summary["silence_guard"]["passed"])
        repeat = summary["repeats"][0]
        self.assertEqual(repeat["successful_cases"], ["public-a"])
        self.assertEqual(repeat["public_failure_ids"], ["public-b"])
        self.assertEqual(repeat["cases"][0]["cer"]["substitutions"], 1)
        self.assertEqual(repeat["cases"][1]["cer"]["deletions"], 2)
        self.assertEqual(repeat["cases"][0]["models"]["asr"], "asr@fixture")
        self.assertIn("source_tree_sha256", repeat["cases"][0]["revision"])
        rescored = self.root / "rescored.json"
        result = self.command("summarize", self.manifest, "--runs", output / "repeat-01", output / "repeat-02", "--out", rescored)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(rescored.read_text(encoding="utf-8"))["objective"]["median_failure_adjusted_cer"], 3 / 5)

    def test_silence_insertions_fail_the_hard_guard_without_entering_speech_cer(self):
        cases = [row for row in self.cases if row["id"] in {"public-a", "contract-silence-5s"}]
        output = self.root / "silence"
        result = self.measure(output, cases=cases, silence_text="uh")
        self.assertEqual(result.returncode, 1, result.stderr)
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["objective"]["public_reference_chars"], 3)
        self.assertEqual(summary["objective"]["median_failure_adjusted_cer"], 1 / 3)
        self.assertEqual(summary["silence_guard"]["insertions_by_repeat"], [2])
        self.assertFalse(summary["silence_guard"]["passed"])

    def test_timed_out_silence_case_fails_guard_even_without_insertions(self):
        cases = [row for row in self.cases if row["id"] in {"public-a", "contract-silence-5s"}]
        output = self.root / "timeout-silence"
        result = self.measure(output, cases=cases, failure_ids=())
        self.assertEqual(result.returncode, 0, result.stderr)
        sidecar_path = output / "repeat-01" / "contract-silence-5s.run.json"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["status"] = "timeout"
        sidecar["error"] = "fixture timeout"
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        report = self.root / "timeout-summary.json"
        result = self.command("summarize", self.manifest, "--runs", output / "repeat-01", "--out", report)
        self.assertEqual(result.returncode, 1, result.stderr)
        guard = json.loads(report.read_text(encoding="utf-8"))["silence_guard"]
        self.assertEqual(guard["insertions_by_repeat"], [0])
        self.assertEqual(guard["failed_ids_by_repeat"], [["contract-silence-5s"]])
        self.assertFalse(guard["passed"])

    def test_summarize_rejects_tampered_or_missing_run_records(self):
        output = self.root / "valid"
        result = self.measure(output, repeats=2)
        self.assertEqual(result.returncode, 1, result.stderr)
        run_dirs = [output / "repeat-01", output / "repeat-02"]
        sidecar_path = run_dirs[1] / "public-a.run.json"
        original_sidecar = sidecar_path.read_bytes()
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["revision"]["source_tree_sha256"] = "different-candidate"
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        result = self.command("summarize", self.manifest, "--runs", *run_dirs, "--out", self.root / "mixed-candidates.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("mix source trees", result.stderr)
        sidecar_path.write_bytes(original_sidecar)
        transcript_path = run_dirs[0] / "public-a.json"
        original_transcript = transcript_path.read_bytes()
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        transcript["segments"][0]["text"] = "changed"
        transcript_path.write_text(json.dumps(transcript), encoding="utf-8")
        result = self.command("summarize", self.manifest, "--runs", *run_dirs, "--out", self.root / "bad.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("output hash does not match", result.stderr)
        transcript_path.write_bytes(original_transcript)
        transcript_path.unlink()
        result = self.command("summarize", self.manifest, "--runs", *run_dirs, "--out", self.root / "absent-output.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("success sidecar has no transcript output", result.stderr)
        transcript_path.write_bytes(original_transcript)
        (run_dirs[0] / "public-a.run.json").unlink()
        result = self.command("summarize", self.manifest, "--runs", *run_dirs, "--out", self.root / "missing.json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing expected run sidecar", result.stderr)


if __name__ == "__main__":
    unittest.main()
