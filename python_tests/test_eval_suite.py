import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evaluate_suite", ROOT / "scripts/evaluate-suite.py")
EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVAL)
PREP_SPEC = importlib.util.spec_from_file_location("prepare_eval", ROOT / "scripts/prepare-eval.py")
PREP = importlib.util.module_from_spec(PREP_SPEC)
PREP_SPEC.loader.exec_module(PREP)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvalSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio.wav"
        self.audio.write_bytes(b"deterministic audio fixture")
        self.reference = self.root / "reference.txt"
        self.reference.write_text("hello world\n", encoding="utf-8")
        self.case = self.make_case()
        self.manifest = self.root / "manifest.jsonl"
        self.write_manifest([self.case])

    def tearDown(self):
        self.temp.cleanup()

    def make_case(self, case_id="case-a", split="heldout", group="recording-a", tuning=False,
                  status="source-labeled", reference_text="hello world\n", reference_language="en"):
        self.reference.write_text(reference_text, encoding="utf-8")
        return {
            "id": case_id,
            "corpus": "fixture-corpus",
            "group": group,
            "split": split,
            "used_for_tuning": tuning,
            "audio": {"path": "audio.wav", "sha256": digest(self.audio), "duration": 10.0},
            "selection": {"start": 2.5, "end": 4.5},
            "language_hint": None,
            "reference_language": reference_language,
            "chinese_script": "original",
            "reference": {
                "path": "reference.txt",
                "sha256": digest(self.reference),
                "status": status,
                **({"reviewer": {"id": "reviewer-1", "method": "audio checked"}} if status == "checked" else {}),
            },
            "provenance": {
                "source_id": "fixture-1",
                "source_revision": "fixture-v1",
                "license": "test fixture",
                "source_url": None,
                "detail": {},
            },
        }

    def write_manifest(self, cases):
        self.manifest.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")

    def transcript(self, case=None, text="hello world", start=2.6, end=4.0, active=None, speaker="speaker_0"):
        case = case or self.case
        active = ["speaker_0"] if active is None else active
        return {
            "schemaVersion": 1,
            "source": {
                "path": str(self.audio.resolve()),
                "duration": 10.0,
                "selection": {"start": 2.5, "end": 4.5},
            },
            "segments": [{"start": start, "end": end, "text": text, "speaker": speaker, "activeSpeakers": active}] if text else [],
            "warnings": [],
            "timing": "forced-aligned",
            "models": {"asr": "asr@abc123", "diarization": "diarization@def456", "alignment": "alignment@789abc"},
        }

    def test_literal_character_edit_counts_for_substitution_deletion_and_insertion(self):
        self.assertEqual(EVAL.edit_counts(list("cat"), list("cut")), {
            "reference_units": 3, "hypothesis_units": 3, "edits": 1,
            "substitutions": 1, "deletions": 0, "insertions": 0, "rate": 1 / 3,
        })
        self.assertEqual(EVAL.edit_counts(list("cat"), list("ct")), {
            "reference_units": 3, "hypothesis_units": 2, "edits": 1,
            "substitutions": 0, "deletions": 1, "insertions": 0, "rate": 1 / 3,
        })
        self.assertEqual(EVAL.edit_counts(list("ct"), list("cat")), {
            "reference_units": 2, "hypothesis_units": 3, "edits": 1,
            "substitutions": 0, "deletions": 0, "insertions": 1, "rate": 1 / 2,
        })
        self.assertEqual(EVAL.edit_counts(list("hello"), list("hello"))["rate"], 0)

    def test_nfkc_casefold_and_mixed_token_policy_are_observable(self):
        self.assertEqual(EVAL.normalize_chars("ＡＢＣ，中文！"), "abc中文")
        self.assertEqual(EVAL.mixed_tokens("你好 Open AI 2026"), ["你", "好", "open", "ai", "2026"])

    def test_segment_boundaries_keep_english_words_separate(self):
        transcript = self.transcript(text="hello")
        transcript["segments"].append({"start": 4.1, "end": 4.4, "text": "world", "speaker": "speaker_0", "activeSpeakers": ["speaker_0"]})
        self.assertEqual(EVAL.output_hypothesis(transcript), "hello world")
        result = EVAL.score_case(self.case, self.root, transcript, None)
        self.assertEqual(result["metrics"]["word_error_rate"]["edits"], 0)
        self.assertEqual(result["metrics"]["cer"]["edits"], 0)

    def test_empty_reference_reports_insertions_and_hallucination_events_without_cer(self):
        empty_case = self.make_case(reference_text="", reference_language="und")
        nonempty = self.transcript(text="uh")
        observed = EVAL.score_case(empty_case, self.root, nonempty, None)["metrics"]
        self.assertIsNone(observed["cer"]["rate"])
        self.assertEqual(observed["cer"]["insertions"], 2)
        self.assertEqual(observed["hallucinated_characters"], 2)
        self.assertEqual(observed["hallucination_events"], 1)
        silent = self.transcript(text="")
        observed_silence = EVAL.score_case(empty_case, self.root, silent, None)["metrics"]
        self.assertIsNone(observed_silence["cer"]["rate"])
        self.assertEqual(observed_silence["hallucinated_characters"], 0)
        self.assertEqual(observed_silence["hallucination_events"], 0)

    def test_selection_mismatch_bad_segments_and_speaker_sets_are_rejected(self):
        wrong_selection = self.transcript()
        wrong_selection["source"]["selection"]["start"] = 2.0
        with self.assertRaisesRegex(EVAL.SuiteError, "selection does not match"):
            EVAL.validate_transcript(wrong_selection, self.case, self.root)
        zero_length = self.transcript()
        zero_length["segments"][0]["end"] = zero_length["segments"][0]["start"]
        with self.assertRaisesRegex(EVAL.SuiteError, "segment bounds"):
            EVAL.validate_transcript(zero_length, self.case, self.root)
        wrong_speakers = self.transcript(active=["speaker_0", "speaker_1"], speaker="speaker_0")
        with self.assertRaisesRegex(EVAL.SuiteError, "speaker must match"):
            EVAL.validate_transcript(wrong_speakers, self.case, self.root)

    def test_audio_and_reference_hash_tampering_fail_manifest_validation(self):
        self.audio.write_bytes(b"changed audio")
        with self.assertRaisesRegex(EVAL.SuiteError, "audio SHA-256 mismatch"):
            EVAL.read_manifest(self.manifest)
        self.audio.write_bytes(b"deterministic audio fixture")
        self.reference.write_text("changed reference", encoding="utf-8")
        with self.assertRaisesRegex(EVAL.SuiteError, "reference SHA-256 mismatch"):
            EVAL.read_manifest(self.manifest)

    def test_cross_split_group_and_audio_aliases_are_rejected(self):
        dev = self.make_case(case_id="dev-copy", split="dev", group="other-recording")
        with self.assertRaisesRegex(EVAL.SuiteError, "audio alias crosses"):
            EVAL.validate_cases([self.case, dev], self.root)
        dev_alias = self.make_case(case_id="dev-copy", split="dev", group="recording-a")
        with self.assertRaisesRegex(EVAL.SuiteError, "recording group .* crosses"):
            EVAL.validate_cases([self.case, dev_alias], self.root)

    def test_tuned_holdout_and_unreviewed_checked_reference_are_rejected(self):
        tuned = self.make_case(tuning=True)
        with self.assertRaisesRegex(EVAL.SuiteError, "heldout cases cannot"):
            EVAL.validate_cases([tuned], self.root)
        checked = self.make_case(status="checked")
        del checked["reference"]["reviewer"]
        with self.assertRaisesRegex(EVAL.SuiteError, "checked references require reviewer"):
            EVAL.validate_cases([checked], self.root)

    def test_run_and_score_cli_bind_output_selection_models_and_revision(self):
        fake_bun = self.root / "fake-bun"
        fake_bun.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "args=sys.argv[1:]\n"
            "audio=pathlib.Path(args[2]).resolve()\n"
            "start=float(args[args.index('--start')+1])\n"
            "duration=float(args[args.index('--duration')+1])\n"
            "out=pathlib.Path(args[args.index('--output')+1])\n"
            "value={'schemaVersion':1,'source':{'path':str(audio),'duration':10.0,'selection':{'start':start,'end':start+duration}},'segments':[{'start':start+0.1,'end':start+0.5,'text':'hello world','speaker':'speaker_0','activeSpeakers':['speaker_0']}],'warnings':[],'timing':'forced-aligned','models':{'asr':'asr@abc123','diarization':'diarization@def456','alignment':'alignment@789abc'}}\n"
            "out.write_text(json.dumps(value),encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake_bun.chmod(0o755)
        output_dir = self.root / "run"
        run = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "run", str(self.manifest), "--out", str(output_dir), "--bun", str(fake_bun)], text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        sidecar = json.loads((output_dir / "case-a.run.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["status"], "success")
        self.assertEqual(sidecar["models"]["asr"], "asr@abc123")
        self.assertGreater(sidecar["elapsed_seconds"], 0)
        self.assertIn("python/worker.py", sidecar["revision"]["source_files_sha256"])
        self.assertIn("model-pins.json", sidecar["revision"]["source_files_sha256"])
        report_path = self.root / "report.json"
        score = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "score", str(self.manifest), "--outputs", str(output_dir), "--report", str(report_path), "--split", "heldout"], text=True, capture_output=True)
        self.assertEqual(score.returncode, 0, score.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["cases"][0]["metrics"]["cer"]["edits"], 0)
        self.assertEqual(report["cases"][0]["runtime"]["selected_audio_seconds"], 2.0)
        self.assertFalse(report["release_gate"]["ready"])

    def test_sidecar_with_wrong_language_condition_is_rejected(self):
        fake_bun = self.root / "fake-bun"
        fake_bun.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "args=sys.argv[1:]\n"
            "audio=pathlib.Path(args[2]).resolve()\n"
            "start=float(args[args.index('--start')+1])\n"
            "duration=float(args[args.index('--duration')+1])\n"
            "out=pathlib.Path(args[args.index('--output')+1])\n"
            "value={'schemaVersion':1,'source':{'path':str(audio),'duration':10.0,'selection':{'start':start,'end':start+duration}},'segments':[{'start':start+0.1,'end':start+0.5,'text':'hello world','speaker':'speaker_0','activeSpeakers':['speaker_0']}],'warnings':[],'timing':'forced-aligned','models':{'asr':'asr@abc123','diarization':'diarization@def456','alignment':'alignment@789abc'}}\n"
            "out.write_text(json.dumps(value),encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake_bun.chmod(0o755)
        output_dir = self.root / "run"
        subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "run", str(self.manifest), "--out", str(output_dir), "--bun", str(fake_bun)], capture_output=True)
        sidecar_path = output_dir / "case-a.run.json"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        output_path = output_dir / "case-a.json"
        output_path.write_text(json.dumps(self.transcript()), encoding="utf-8")
        sidecar["status"] = "success"
        sidecar["models"] = self.transcript()["models"]
        sidecar["output_sha256"] = digest(output_path)
        sidecar["argv"][sidecar["argv"].index("original")] = "zh-TW"
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "score", str(self.manifest), "--outputs", str(output_dir), "--report", str(self.root / "bad-report.json")], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("arguments do not match manifest", result.stderr)

    def test_timeout_interrupts_process_group_and_records_failure(self):
        slow_bun = self.root / "slow-bun"
        slow_bun.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n", encoding="utf-8")
        slow_bun.chmod(0o755)
        output_dir = self.root / "timeout-run"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "run", str(self.manifest), "--out", str(output_dir), "--bun", str(slow_bun), "--timeout", "0.1"], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        sidecar = json.loads((output_dir / "case-a.run.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["status"], "timeout")
        self.assertIn("SIGINT", sidecar["error"])

    def test_user_interrupt_stops_isolated_cli_process_group(self):
        marker = self.root / "child-stopped"
        child_ready = self.root / "child-ready"
        ready = self.root / "cli-ready"
        cli_pid = self.root / "cli-pid"
        child_script = self.root / "child-worker.py"
        child_script.write_text(
            "import pathlib, signal, sys, time\n"
            "stopped=pathlib.Path(sys.argv[1])\n"
            "signal.signal(signal.SIGINT, lambda *_: (stopped.write_text('stopped'), sys.exit(0)))\n"
            "pathlib.Path(sys.argv[2]).write_text('ready')\n"
            "while True: time.sleep(1)\n",
            encoding="utf-8",
        )
        fake_bun = self.root / "interrupt-bun"
        fake_bun.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, signal, subprocess, sys, time\n"
            "child=subprocess.Popen([sys.executable," + repr(str(child_script)) + "," + repr(str(marker)) + "," + repr(str(child_ready)) + "])\n"
            "pathlib.Path(" + repr(str(cli_pid)) + ").write_text(str(__import__('os').getpid()))\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "while not pathlib.Path(" + repr(str(child_ready)) + ").exists(): time.sleep(0.01)\n"
            "pathlib.Path(" + repr(str(ready)) + ").write_text('ready')\n"
            "while True: time.sleep(1)\n",
            encoding="utf-8",
        )
        fake_bun.chmod(0o755)
        output_dir = self.root / "interrupt-run"
        runner = subprocess.Popen([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "run", str(self.manifest), "--out", str(output_dir), "--bun", str(fake_bun)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(1500):
                if ready.exists():
                    break
                time.sleep(0.02)
            if not ready.exists():
                runner.send_signal(signal.SIGINT)
                _, startup_error = runner.communicate(timeout=12)
                log_path = output_dir / "case-a.stderr.log"
                self.fail(f"fake CLI child did not start; runner exit={runner.returncode}, stderr={startup_error}, log={log_path.read_text(encoding='utf-8') if log_path.exists() else 'missing'}")
            runner.send_signal(signal.SIGINT)
            _, stderr = runner.communicate(timeout=12)
            self.assertEqual(runner.returncode, 130, stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "stopped")
            sidecar = json.loads((output_dir / "case-a.run.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["status"], "interrupted")
        finally:
            if runner.poll() is None:
                runner.send_signal(signal.SIGINT)
                try:
                    runner.communicate(timeout=12)
                except subprocess.TimeoutExpired:
                    runner.kill()
                    runner.communicate(timeout=5)
            if cli_pid.exists():
                try:
                    os.killpg(int(cli_pid.read_text(encoding="utf-8")), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_sigint_during_popen_startup_is_deferred_until_child_is_owned(self):
        marker = self.root / "startup-child-stopped"
        fake_bun = self.root / "startup-interrupt-bun"
        fake_bun.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGINT, lambda *_: (pathlib.Path(" + repr(str(marker)) + ").write_text('stopped'), sys.exit(0)))\n"
            "while True: time.sleep(1)\n",
            encoding="utf-8",
        )
        fake_bun.chmod(0o755)
        output_dir = self.root / "startup-interrupt-run"
        original_popen = subprocess.Popen
        spawned = []

        def interrupt_before_return(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            argv = args[0]
            if isinstance(argv, (list, tuple)) and argv and str(argv[0]) == str(fake_bun):
                spawned.append(process)
                os.kill(os.getpid(), signal.SIGINT)
            return process

        subprocess.Popen = interrupt_before_return
        try:
            result = EVAL.run_suite(SimpleNamespace(
                manifest=self.manifest,
                case=None,
                split=None,
                timeout=5.0,
                out=output_dir,
                bun=str(fake_bun),
            ))
            child_returncode = spawned[0].poll()
        finally:
            subprocess.Popen = original_popen
            if spawned and spawned[0].poll() is None:
                try:
                    os.killpg(spawned[0].pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                spawned[0].wait(timeout=5)
        self.assertEqual(result, 130)
        self.assertIsNotNone(child_returncode, "the runner must reap the child interrupted during startup")
        sidecar = json.loads((output_dir / "case-a.run.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["status"], "interrupted")

    def test_private_import_uses_only_complete_blocks_and_preflights_collisions(self):
        draft = self.root / "draft.md"
        draft.write_text(
            "notes that are not transcript\n**逐字稿**\n"
            "00:00 \\- 00:43 主持人：開場\n延續第一段\n\n"
            "00:43 \\- 00:50 嘉維：回答\n"
            "00:50 \\- 01:21 主持人：追問\n"
            "01:21 \\- 01:36 嘉維：後續不匯入\n",
            encoding="utf-8",
        )
        text, speakers, blocks = PREP.extract_opening(draft.read_text(encoding="utf-8"))
        self.assertEqual(blocks, ["0-43", "43-50", "50-81"])
        self.assertEqual(speakers, ["主持人", "嘉維"])
        self.assertIn("延續第一段", text)
        self.assertNotIn("主持人", text)
        self.assertNotIn("notes that are not transcript", text)
        self.assertNotIn("後續不匯入", text)

        audio = self.root / "meeting.m4a"
        audio.write_bytes(b"private audio bytes")
        fake_ffprobe = self.root / "ffprobe"
        fake_ffprobe.write_text("#!/bin/sh\nprintf '90.0\\n'\n", encoding="utf-8")
        fake_ffprobe.chmod(0o755)
        output = self.root / "private-output"
        env = dict(os.environ)
        env["PATH"] = str(self.root) + os.pathsep + env.get("PATH", "")
        command = [sys.executable, str(ROOT / "scripts/prepare-eval.py"), "private-draft", "--source-markdown", str(draft), "--audio", str(audio), "--output-dir", str(output)]
        imported = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        manifest = output / "manifest.private.jsonl"
        case = json.loads(manifest.read_text(encoding="utf-8"))
        EVAL.validate_cases([case], output)
        self.assertEqual(case["chinese_script"], "zh-TW")
        self.assertEqual(case["reference"]["status"], "draft")
        self.assertEqual(case["provenance"]["detail"]["draft_sha256"], digest(draft))

        collision_output = self.root / "collision-output"
        (collision_output / "audio").mkdir(parents=True)
        (collision_output / "audio" / "private-opening.m4a").write_bytes(b"preexisting")
        command[-1] = str(collision_output)
        collision = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertEqual(collision.returncode, 2)
        self.assertIn("already exists", collision.stderr)
        self.assertFalse((collision_output / "references" / "private-opening.txt").exists())
        self.assertFalse((collision_output / "manifest.private.jsonl").exists())

    def test_missing_output_fails_and_draft_does_not_qualify_for_gate(self):
        output_dir = self.root / "empty-outputs"
        output_dir.mkdir()
        report_path = self.root / "missing-report.json"
        missing = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "score", str(self.manifest), "--outputs", str(output_dir), "--report", str(report_path)], text=True, capture_output=True)
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["missing_outputs"], ["case-a"])
        draft = self.make_case(split="dev", group="draft-group", tuning=True, status="draft")
        self.write_manifest([draft])
        transcript_path = output_dir / "case-a.json"
        transcript_path.write_text(json.dumps(self.transcript(draft)), encoding="utf-8")
        report_path = self.root / "draft-report.json"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-suite.py"), "score", str(self.manifest), "--outputs", str(output_dir), "--report", str(report_path), "--require-checked"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["release_gate"]["eligible_checked_speech_cases"], [])
        self.assertFalse(report["release_gate"]["thresholds_configured"])


if __name__ == "__main__":
    unittest.main()
