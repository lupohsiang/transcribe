# Local transcription CLI

1. `how` over the affected subsystem. Skipped because this is an empty greenfield workspace. Ground the external model APIs instead.
2. `architect` for parallel design exploration. Compare all-in-one VibeVoice with a multilingual ASR and persistent diarizer pipeline.
3. Write the throughput checkpoint as four todo items.
   - Blocking first steps. Read upstream APIs, inspect hardware and the real recording, select the data shape.
   - Independent workstreams. One owner writes the feature. Parent checks upstream compatibility and real inference prerequisites.
   - Shared mutable state. One source writer; per-run temporary directories and immutable model snapshots.
   - Smallest safe decomposition. One implementation owner keeps the Python protocol, Effect capability, CLI, and tests consistent.
4. Delegate code-writing to a subagent. Parent reviews every source file and runs the CLI.
5. Verify on the matching surface. Run help, invalid input, machine output, cancellation tests, and real audio inference.
6. Rebase into small, ordered commits. Skipped because no Git repository or remote exists.
7. If the design is contested, `interrogate` before shipping. Revisit if the candidate comparison leaves a contested decision.
8. Run Opening a PR. Skipped because no repository or remote exists and publishing was not requested.

## Acceptance

Bun and Effect own the CLI and capability. A local MLX backend recognizes speech and labels speakers. Transcription emits versioned JSON, text, or SRT. Diagnostics use stderr. Help and errors are actionable. Output files are protected against accidental replacement. Downloads are explicit; inference is offline. Real audio is used for proof, with test limitations recorded.

## Observed constraints

The target is Apple Silicon with 18 GiB RAM and macOS 14.8.8. The provided input is 5,365.674667 seconds, about 89 minutes. Batch VibeVoice silently clips at 59 minutes in MLX Audio. Independent chunks cannot promise consistent speaker identities. A bounded-context diarizer is required for this recording.

## Checkpoint after implementation

The pipeline candidate was selected after two independent sketches and a separate cross-judge. The implementation uses a single writer and per-run temporary state. Thirteen Bun tests and five Python tests passed. Type checking, Biome, and the Bun build passed. Actual help and exported JSON Schema were inspected. The pinned models are downloaded. Real-audio inference remains pending the Python runtime download.

The decoder estimates differ slightly. The initial `afinfo` estimate was 5,365.674667 seconds; FFprobe reports a container duration of 5,365.738667 seconds. Transcript source metadata uses the FFprobe value. The 120-second smoke run completed in 108.94 seconds with 28 text segments and three generic speaker labels. The wording and speaker labels have not been manually validated. Runtime evidence and executed command records are in `artifacts/execution-report.json`.

## Completion

The built CLI processed the entire recording successfully in 1,267.11 seconds. JSON, text, and SRT exports were validated. A missing-text diagnostic was added after observing 446 empty ASR results, and verified on a second real 120-second run. The unmodified full-run output is preserved. See `docs/VERIFICATION.md` and `artifacts/execution-report.json` for exact results and limitations. No repository or PR was created because this workspace had no Git repository or remote.
