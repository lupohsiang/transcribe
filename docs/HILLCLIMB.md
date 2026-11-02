# Failure-aware speech evaluation hill climb

The controlled development comparison reduces failure-adjusted character error rate from 23.78% to 13.99%, a 41.18% relative reduction. The accepted fix preserves recognized speech when the pinned aligner rounds a final timestamp slightly past the selected audio boundary.

This measures agreement with source transcripts. It does not establish accuracy on Taiwan workplace recordings. The private interview draft remains an advisory reference.

## Frozen measurement

The target is at least a 20% relative reduction after at least three measured hypotheses. Accuracy and completion take priority. Silence must produce no inserted text, and regression tests must pass.

The development set contains 12 public ASCEND clips, one generated silence clip, and the private draft's 81-second opening. The primary metric pools normalized character edits over 286 public reference characters. A failed or timed-out speech case contributes its full reference as deletions. Private draft agreement and silence are separate results.

Normalization uses NFKC, case folding, and Unicode letters and numbers. The frozen scorer counts source `[UNK]` markers literally after normalization. No reference text, normalization rule, or timeout changed between candidates.

Baseline and the accepted candidate each ran three times with a 120-second timeout per case. GPU inference ran serially. Each saved output has a sidecar binding the audio, case, transcript, model pins, and source hashes.

| Measurement | Baseline | Accepted alignment fix |
| --- | --- | --- |
| Median failure-adjusted CER | 68/286, 23.78% | 40/286, 13.99% |
| CER in each repeat | 23.78%, 23.78%, 27.27% | 13.99%, 13.99%, 13.99% |
| Median public completion | 11/12 | 12/12 |
| Full-suite completion | One persistent failure, one additional timeout | 14/14 in every repeat |
| Silence insertions | 0 in every repeat | 0 in every repeat |
| Private draft disagreement | 32.79% median | 32.79% in every repeat |

The improvement comes from completion. It does not change the recognizer's wording. The selected 2.876-second clip previously failed because its final aligned timestamp was 2.880 seconds. The pinned aligner uses 80-millisecond timestamp classes. The fix permits at most half a class of end overshoot and clamps that end to the audio boundary. Invalid starts, nonfinite values, reversed intervals, nonmonotone intervals, and larger overshoots still fail.

## Trials

The canonical append-only decision log is `.audit/hillclimb/decision.tsv`. A versioned [decision-log export](HILLCLIMB-DECISIONS.tsv) preserves the completed run. Rejected candidate patches remain under `.audit/hillclimb/` for inspection.

| Hypothesis | Measured repeats | Failure-adjusted CER | Decision |
| --- | --- | --- | --- |
| Bound small timestamp quantization overshoots | 3 | 13.99% | Keep, commit `b60e6fa` |
| Separate recognition and alignment model lifetimes | 1 screening run | 13.99% | Revert, no accuracy improvement |
| Ask the recognizer to preserve verbatim wording | 1 screening run | 15.38% | Revert, four additional character edits |

Both screening candidates completed all 14 cases with zero silence insertions. The model-lifetime candidate produced identical segments to the accepted version. The verbatim prompt also increased private draft disagreement from 32.79% to 34.01%. Neither candidate justified confirmation runs. Only the accepted improvement has three repeated measurements.

## Reserved comparison

Ten additional cases were selected before candidate evaluation. Eight come from ASCEND validation speakers 7, 12, and 20. Development speakers are 3 and 17. The other two are single-voice, word-bounded AMI ES2004a excerpts from fixed 100–120 and 320–340 second windows. Both AMI excerpts belong to one meeting-series group.

Selection uses source IDs, language, duration, speaker IDs, and annotation boundaries. It does not use candidate outputs. These are source-labeled references without local human checking. The combined manifest passes split and audio-leakage validation.

The reserved ASCEND IDs are `00001`, `00003`, `00004`, `00006`, `00095`, `00096`, `00109`, and `00340`. Their case IDs use the prefix `ascend-validation-`. The AMI case IDs are `ami-es2004a-100` and `ami-es2004a-320`.

The reserved comparison ran once per version with the same 120-second timeout, explicit Bun and Python paths, and model pins. Baseline produced 108 edits over 616 reference characters, or 17.53% failure-adjusted CER. The selected version produced 68 edits, or 11.04%. That is a 37.04% relative reduction. Completion rose from 9/10 to 10/10.

| Reserved slice | Cases | Baseline character edits | Final character edits |
| --- | --- | --- | --- |
| ASCEND Chinese | 2 | 0/35 | 0/35 |
| ASCEND English | 2 | 5/63 | 5/63 |
| ASCEND mixed | 4 | 84/151 | 44/151 |
| AMI English | 2 | 19/367 | 19/367 |

The recovered mixed case, `ascend-validation-00340`, had failed baseline's timestamp-boundary validation. Its 58 reference characters counted as deletions. The final version succeeds with 18 character edits, accounting for all 40 fewer reserved edits. The other nine cases have unchanged character edit counts. This is one paired check on a small set, without a confidence claim. The reserved set contains no silence case. Silence evidence comes from the development repeats.

The reserved cases are now exposed. Further tuning requires new reserved cases.

## Evidence and reproduction

The frozen runner is `scripts/hillclimb-eval.py` at commit `b6a1c8f`. The development manifest SHA-256 is `5d79c6c958730c9816e42f27bf6633f8de8910425bb9aa37733b8fd8576b1828`. The reserved manifest SHA-256 is `cf8a3dfe408961edf69635d41df285929b41a222e62fbb8c9188e4ca8f29f92d`.

Local evidence lives under ignored `artifacts/hillclimb/`.

- `baseline/summary.json` records the controlled baseline.
- `attempt1/summary.json` records the three accepted measurements.
- `alignment-probe.json` records the quantized-end failure.
- `case-freeze.json` records reserved IDs and speaker separation.
- `prepare-holdout.py` records deterministic reserved-case preparation.
- `attempt2/summary.json` and `attempt3/summary.json` record rejected screening runs.
- `holdout-baseline-2/summary.json` and `holdout-final/summary.json` record the reserved paired comparison.

The versioned [aggregate results](HILLCLIMB-RESULTS.json) preserve the protocol, metrics, and source hashes without transcript text. The first reserved launcher attempt under `holdout-baseline/` failed to find Bun before inference. It is preserved as an invalid launcher attempt and excluded from the paired result.

Use a new output directory for each measurement.

```sh
python3 scripts/hillclimb-eval.py measure artifacts/hillclimb/development.jsonl \
	--out artifacts/hillclimb/new-measurement --repeats 3 --timeout 120
```

The runner exits nonzero when cases fail and still writes the summary. Existing output directories are protected. The [evaluation guide](EVAL.md) describes dataset preparation and saved-output scoring.

## Limits and verification

The host showed substantial memory pressure during earlier measurements. The unchanged baseline also reproduced intermittent test timeouts. No test timeout was increased. Runtime is therefore not a performance claim.

Final `bun run check`, 17 Bun tests, 46 Python tests, and `bun run build` pass with native process access. Two preceding sandboxed final runs timed out in CLI process tests. Their logs remain under `artifacts/hillclimb/final-regression.log` and `final-regression-2.log`. The cause of those timeouts is not proven. Native passing logs use the prefix `final-native-`.

The retained task digest reports an initially green baseline gate, but no durable log of that initial run was found during closeout. The decision log qualifies that historical claim. Durable serial logs verify the accepted candidate, and the final native gate passes.

This set is small. Speaker attribution and timestamp accuracy remain unscored. Published source labels contain conventions that may disagree with useful output. The private draft is unverified and cannot establish recognition accuracy. The release gate remains not ready without checked heldout references and agreed acceptance thresholds.

Pstack's Prove It Works principle required real audio comparisons. Sequence Work into Verifiable Units kept each candidate separate and required a decision before the next change.
