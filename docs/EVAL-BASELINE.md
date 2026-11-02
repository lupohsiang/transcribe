# Starter evaluation baseline

Measured locally on 2026-10-29. The evaluation implementation is runnable. The transcription pipeline did not complete every case within the configured timeout.

The canonical attempt is `artifacts/eval-data/run-baseline-2/`. All 14 cases were attempted with a 90-second per-case timeout. Ten produced valid, bound transcripts. Four timed out. The scorer wrote `artifacts/eval-data/run-baseline-2-report.json` and exited with status 1 for missing outputs. No failed case was converted into a zero-error score.

| Case bucket | Attempted | Scored | Result on scored cases only |
| --- | --- | --- | --- |
| ASCEND Chinese | 3 | 3 | 7 character edits over 51 reference characters, 13.73% CER. |
| ASCEND English | 3 | 2 | 7 word edits over 17 reference words, 41.18% WER. One timeout. |
| ASCEND mixed Chinese-English | 6 | 3 | 8 mixed-token edits over 44 reference tokens, 18.18% mixed error rate. Three timeouts. |
| Generated silence | 1 | 1 | No inserted characters or transcript events. CER is undefined on an empty reference. |
| Private draft opening | 1 | 1 | 81 character edits over 247 draft characters, 32.79% provisional reference disagreement. |

These are conditional metrics for successful cases, not full-suite accuracy. ASCEND labels come from the published corpus and have not been independently checked here. The private result measures agreement with an unverified draft, not recognition accuracy. The public sample has only one independent group because speakers recur across its sessions. It does not support a confidence claim about Taiwan workplace conversations.

## Failures and operating conditions

The timeouts were `ascend-00002`, `ascend-00055`, `ascend-00132`, and `ascend-00143`. Each has a run sidecar and stderr log in the canonical attempt directory. Model settings and case selection were not changed to make these examples pass.

A separate direct reproduction of `ascend-00002` reached a worker error stating that alignment returned invalid or out-of-window timestamps. That diagnostic is distinct from the canonical attempt's timeout. The production worker was not modified as part of creating the evaluation.

The machine showed substantial memory pressure during verification, including about 19.8 GB of occupied swap in one observation. Run times varied widely, so these timings should not be treated as a hardware performance benchmark. The runner records elapsed time and source/model provenance for each attempt.

An earlier attempt under `run-baseline-1/` was interrupted during diagnosis and is not the canonical baseline. The preflight directory contains separate public-audio and silence probes.

## Reproduce the report

From the repository root, choose a fresh report path because the scorer protects existing files.

```sh
python3 scripts/evaluate-suite.py validate artifacts/eval-data/manifest.private.jsonl
python3 scripts/evaluate-suite.py score artifacts/eval-data/manifest.private.jsonl \
  --outputs artifacts/eval-data/run-baseline-2 \
  --report artifacts/eval-data/run-baseline-2-rescore.json
```

The score command is expected to exit with status 1 until those four outputs are available from valid runs. Do not remove the failed cases from the manifest to obtain a successful exit.

Use the [evaluation guide](EVAL.md) to prepare a clean copy and run a new attempt. The suite reports its release gate as not ready because no checked heldout speech references and acceptance thresholds have been established. Synthetic silence cannot satisfy that requirement. Speaker and timestamp accuracy remain unscored.

## Verification record

The 15 evaluator tests passed after review fixes. They exercise literal edit counts, transcript validation, changed hashes, development/holdout leakage, draft exclusion, missing outputs, saved-run binding, private transcript extraction, timeout cleanup, and interruption cleanup including the process-creation boundary. Final project check results are recorded in the implementation plan.

Pstack's Model the Domain principle led to explicit case groups and reference states. Build the Lever led to repeatable preparation and scoring commands. Prove It Works required actual audio runs and preservation of unsuccessful attempts rather than claiming a passing accuracy benchmark.
