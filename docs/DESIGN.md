# Architecture decision

Use a Bun CLI over one schema-defined Effect transcription capability. A local Python MLX adapter hides decoding, diarization, recognition windows and alignment, and model management. No cloud infrastructure is needed.

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

`Transcript` contains the source duration, model revisions, an ordered array of speaker turns, and warnings. Each turn has absolute start/end seconds, text, a nullable speaker, and the active-speaker set. Overlap has no single attributed speaker. New timestamps derive from word or character alignment. Older `speaker-turn` transcripts retain their original meaning. Unknown attribution uses an empty active-speaker set and never discards recognized text.

The Python worker decodes the selected audio track through an FFmpeg pipe, reading one second of mono 16 kHz PCM at a time. It retains one Nemotron speaker-cache/FIFO state for the recording. Recognition windows never reset that state. All three model weights stay resident, with inference running serially. Qwen3-ASR recognizes continuous windows of at most 30 seconds, and Qwen3-ForcedAligner estimates word or character times. The worker maps aligned items back into the original text before assigning speaker activity. Zero-duration items merge into adjacent timed text.

Pending audio and speaker probabilities have bounded storage. The audio capacity includes a 30-second recognition window, five seconds to decide whether to rebalance a short final tail, and the diarizer's configured lookahead. The default speaker configuration can require about 58 seconds of decoded audio before the first 30-second window is ready. Probability history keeps enough past and future frames to filter runs shorter than 100 ms before merging gaps up to 150 ms across feed boundaries. EOF flushes the speaker model once and processes the remaining audio. Only the current recognition window is written to a temporary WAV.

Worker stdout contains private NDJSON header, segment, and completion records. The transcriber validates model pins, source metadata, segment bounds, order, and completion before returning success. An awaited segment observer provides backpressure for `--stream`. Default output remains a complete transcript, and file exports publish atomically. Streaming stdout can contain a valid prefix when a later window fails. Transcript storage grows with text length; audio storage does not grow with recording length.

The CLI owns presentation and exit codes. The Effect service owns the application contract and typed failures. The worker adapter owns third-party representations. Effect Schema validates worker output at the boundary. Setup downloads assets explicitly and records immutable revisions. Runtime uses local snapshots and offline flags. Each run owns its temporary files and subprocess lifetime.

The public result retains the simple speaker/start/end/content structure. Speaker identity comes from continuous model state. No cross-window matching service, daemon, model-provider framework, or cloud service is needed.

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

## Recognition context

The first implementation fed every speaker activity fragment to ASR. Most recognized fragments in the supplied recording were under two seconds. A local comparison with the same model showed that longer context changed garbled fragments into more coherent Chinese. Recognition now covers the full selection independently of detected speaker activity.

Three candidate designs and a separate judge selected nonoverlapping continuous windows. A paired ASR and aligner probe peaked at 5,207,811,596 MLX bytes on the 18 GiB Mac, so both models remain loaded during the bounded loop. This avoids an intermediate transcript spool. Overlapping recognition windows were rejected because timestamp midpoint ownership alone does not prove that words are neither lost nor duplicated. Fixed boundaries remain a known limitation. See [transcription quality](QUALITY.md).
