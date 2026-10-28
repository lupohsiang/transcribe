# Architecture decision

Use a Bun CLI over one schema-defined Effect transcription capability. A local Python MLX adapter hides decoding, diarization, ASR slicing, and model management. No cloud infrastructure is needed.

## Candidate comparison

| Criterion, 5 is best | VibeVoice batch | Qwen3 + Nemotron |
| --- | --- | --- |
| Full 89-minute recording | 2 | 4 |
| 18 GiB memory fit | 2 | 5 |
| Complexity hidden behind interface | 3 | 4 |
| Offline reproducibility | 4 | 3 |
| Implementation risk | 3 | 3 |

Two independent architecture candidates and a separate cross-judge selected Qwen3-ASR-1.7B-8bit with Nemotron-3-Diarization-8bit. VibeVoice's MLX batch implementation silently trims above 59 minutes. Running it in chunks requires another speaker reconciliation system. The smaller pipeline models fit this Mac better, subject to measured verification.

## Data shape and ownership

`Transcript` contains the source duration, model revisions, an ordered array of speaker turns, and warnings. Each turn has absolute start/end seconds, text, a nullable speaker, and the active-speaker set. Overlap has no single attributed speaker. Timestamps describe speaker-turn or bounded-slice extents, not word alignment.

Nemotron processes the recording once with its documented bounded speaker-cache/FIFO state. Its generic speaker IDs belong to that invocation. ASR slices must never reset diarization. Partition overlapping turns into intervals and transcribe each interval once. Split long intervals into bounded ASR slices. Release the diarizer before loading ASR.

The CLI owns presentation and exit codes. The Effect service owns the application contract and typed failures. The worker adapter owns third-party representations. Effect Schema validates worker output at the boundary. Setup downloads assets explicitly and records immutable revisions. Runtime uses local snapshots and offline flags. Each run owns its temporary files and subprocess lifetime.

Graft the simple speaker/start/end/content result from the all-in-one candidate. Reject automatic cross-chunk speaker matching, daemons, model-provider frameworks, and cloud packages. They do not help this first recording.

## Principles applied

Model the Domain led to a versioned transcript and interval timeline rather than loose worker JSON. Boundary Discipline puts validation at the CLI and worker protocol. Prove It Works requires actual inference on the supplied recording alongside deterministic CLI tests.

## Sources checked

- https://ratstack.sh/llms.txt
- https://ratstack.sh/lore/error-model
- https://ratstack.sh/lore/hexagonal-architecture
- https://ratstack.sh/skills/add-a-capability
- https://raw.githubusercontent.com/cli-guidelines/cli-guidelines/main/content/_index.md
- https://github.com/Blaizzy/mlx-audio/blob/v0.6.2/mlx_audio/vad/models/nemotron_diarization/README.md
- https://github.com/Blaizzy/mlx-audio/blob/v0.6.2/mlx_audio/stt/models/qwen3_asr/README.md
- https://github.com/Blaizzy/mlx-audio/blob/v0.6.2/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py
