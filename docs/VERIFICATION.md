# Verification

The complete input `新錄音 4.m4a` was processed locally through the built CLI on this Apple Silicon Mac with 18 GiB RAM and macOS 14.8.8. FFprobe reports 5,365.738667 seconds. The selected range was the complete recording, with no 59-minute truncation.

The full run exited successfully in 1,267.11 seconds, about 21 minutes. It processed 2,648 diarized intervals and produced 2,202 nonempty transcript segments, seven model-assigned speaker labels, and 618 segments marked as overlapping. Speech recognition returned no text for 446 intervals. This is a count of detected intervals, not a measured amount of missing speech.

The JSON passed the Effect Schema checks for selected-range containment, ordered timestamps, non-overlap, and speaker-field consistency. Text and SRT exports passed; the SRT contains 2,202 cues. The final recognized segment ends at 5,359.8 seconds. Timestamps describe speaker turns or bounded ASR slices, not words.

Word accuracy and speaker accuracy have not been manually evaluated. Seven labels do not establish that seven people spoke. Overlap markers do not separate simultaneous voices. These outputs are model estimates.

## Files

The linked reports and audio-derived files are local artifacts excluded from Git. They are available in the original workspace, but are not included in a repository clone.

- [Text transcript](../artifacts/recording.txt).
- [Structured JSON](../artifacts/recording.json).
- [SRT subtitles](../artifacts/recording.srt).
- [Output statistics](../artifacts/recording.summary.json).
- [Full-run progress and resource log](../artifacts/recording.log).
- [Executed commands, code hashes, and results](../artifacts/execution-report.json).
- [Unmodified inference output](../artifacts/recording.raw.json).
- [Full-run worker source snapshot](../artifacts/worker.full-run.py).
- [Runtime package and wheel-hash verification](../artifacts/runtime-verification.json).
- [Type, lint, test, and build output](../artifacts/checks.log).

## Diagnostic change after the full run

The full run exposed missing-text intervals that were omitted without a count. The worker now reports that count in JSON warnings and stderr. A second real run on the first 120 seconds succeeded in 21.81 seconds and reported one empty interval among 29 detected intervals. The Python interval tests passed after the change.

The same warning was added to the full transcript using the interval count from its log and the nonempty segment count from its JSON. Its text and timestamps were not altered. The original result is preserved separately, and the worker snapshot matches the hash recorded for the full run.

## Automated checks

Strict TypeScript, Biome, 13 Bun tests, five Python tests, and the Bun build passed. Tests cover actual CLI subprocess behavior and FFmpeg conversion through a protocol fixture, input and output protection, strict numeric options, timestamp offsets, schema validation, SRT formatting, interval partitioning, Ctrl-C cleanup, and Effect interruption reaching the worker. Model inference was tested separately on the actual recording.

Inference used local model snapshots with Hugging Face and Transformers offline flags. No packet capture or operating-system network isolation test was performed. The model revisions are in `model-pins.json`. Setup used the frozen `uv.lock`; four slow package downloads were replaced with mirror copies verified against the official lockfile hashes. The environment contains 39 installed packages.

## Traditional Chinese output

The canonical `recording.json`, `recording.txt`, and `recording.srt` exports now contain Taiwan Traditional Chinese, converted locally with OpenCC `cn` to `tw`. The original exports are preserved as `recording.original.json`, `recording.original.txt`, and `recording.original.srt`; `recording.raw.json` remains the unmodified model output.

Conversion changed the text in 1,512 of 2,202 segments. Segment count, timestamps, speaker fields, source metadata, models, and warnings were compared and preserved. All 2,202 SRT cues remain present. No audio inference was repeated. The conversion configuration, backup hashes, and new output hashes are recorded in `artifacts/recording.zh-tw-conversion.json`. The pre-conversion execution record is preserved as `artifacts/execution-report.before-zh-tw.json`.

Future CLI output defaults to `--chinese-script zh-TW` in every format; `--chinese-script original` preserves the ASR model's text. Tests now include mixed Chinese/English text, contextual character conversion, metadata preservation, the original-text option, and the real CLI projection. All 16 Bun tests, five Python tests, type checking, linting, and the updated build passed. This conversion does not improve recognition or speaker accuracy.

## Native release packaging

On 2026-10-28, built an Apple Silicon Mach-O executable with Bun 1.3.11 and installed the approximately 22 MB release archive through `install.sh`. Verified checksum failures preserve the existing installation, repeated installs and upgrades preserve earlier release directories, unrelated executables are not overwritten, and unexpected archive paths are rejected. Installer tests use paths containing spaces.

Ran the installed command through its symlink from `/private/tmp`, with no Bun executable on PATH. Its own `setup` created a separate Python 3.13.5 environment with the frozen dependencies and reused the existing pinned model snapshots. A 15-second selection of the supplied recording produced four text segments from five diarized intervals, with the omitted interval reported as a warning. Chinese output was Traditional Chinese. The private transcript remains in ignored local artifacts.

The release workflow runs checks and protocol tests on Linux and cross-compiles the Apple Silicon executable. MLX inference and native installation were verified locally; the workflow does not claim to run model inference on Linux.

## Context-preserving recognition

The updated pipeline completed the supplied 5,365.738667-second recording in 595.96 seconds. All 179 continuous recognition windows completed ASR and forced alignment. The validated output contains 2,548 segments, including 272 with unknown speaker attribution and 640 with simultaneous speaker activity. Zero-duration aligned items appeared in every window and retained their text through adjacent timed items. The original transcript was preserved. These counts do not establish recognition or speaker accuracy.

A matching 60-second CLI comparison with an explicit Chinese hint took 20.15 seconds before the fix and 17.88 seconds afterward. The longer-context output replaced several garbled fragments with more coherent phrases. There is no human-checked reference, so no character error rate is claimed. A separate paired-model probe peaked at 5,207,811,596 MLX bytes on the 18 GiB Mac.

The worker regression failed before the fix with eight isolated fragments and passed afterward with the complete expected sentence. The final checks passed 17 Bun tests and 23 Python tests. One CLI integration test exceeded its five-second test timeout during the full inference job; it passed when the final suite ran after inference. The test timeout was not changed.

The pinned aligner supports fewer languages than the recognizer. The worker now excludes Japanese and Korean when their required tokenizer modules are absent. The README and CLI guides state the narrower supported-language contract. Chinese and English remain supported. See [transcription quality](QUALITY.md) for the evaluation procedure and remaining limitations.
