# Transcription quality

Recognition needs enough audio context to distinguish words. The original worker treated each speaker activity fragment as a separate ASR input. In the supplied recording, 70.2% of recognized segments were shorter than two seconds. This also let missed speaker activity exclude audio from recognition.

The worker now recognizes continuous, nonoverlapping windows of at most 30 seconds across the entire selection. Qwen3-ForcedAligner maps the recognized text to word or character times. The worker then assigns speaker labels using the independent diarization timeline. Raw text spans preserve punctuation, spaces, repeated words, and mixed Chinese and English. Traditional Chinese conversion remains the final formatting step.

A local 60-second CLI comparison took 20.15 seconds with the previous pipeline and 17.88 seconds with the new one. The new output retained more text and replaced several garbled fragments with more coherent phrases. This is a diagnostic comparison, not a measured accuracy score. No human-checked reference was available. Private audio and transcript excerpts remain outside Git.

## Timing and attribution

New transcripts use `timing: "forced-aligned"` and record the aligner revision in `models.alignment`. The decoder still accepts older `timing: "speaker-turn"` transcripts. A model-derived timestamp does not prove that the recognized word is correct.

Zero-duration alignment items keep their text by joining an adjacent timed item. The worker reports this in the warnings. It stops on invalid or unmappable alignment rather than inventing timestamps. Unknown speaker attribution uses `speaker: null` and `activeSpeakers: []`. Actual simultaneous speaker activity retains its detected speaker set. A tie between consecutive speakers remains unknown.

Fixed window boundaries can still cut words or sentences. Recognition errors, overlapping speech, technical names, and speaker identity drift remain possible. This frozen build supports Chinese, Cantonese, English, German, Spanish, French, Italian, Portuguese, and Russian. The aligner supports fewer languages than the ASR model. Japanese and Korean require tokenizer dependencies absent from this build and are rejected before recognition when specified explicitly. Other unsupported alignment languages also stop the run. This is a narrower language contract than the original ASR-only pipeline. Chinese and English are covered by the local implementation tests.

## Compare against a checked reference

Use a human-checked excerpt of two to five minutes. Include technical names, interruptions, and at least one window boundary. The reference and both JSON transcripts must cover exactly the same selected audio. Use the same Chinese writing system in the reference and transcript.

```bash
python3 scripts/evaluate-quality.py \
  --reference reference.txt \
  --transcript improved.json \
  --baseline baseline.json
```

Run this command from a source checkout. The script reports normalized character error rate, substitutions, deletions, and insertions. It normalizes Unicode with NFKC, folds case, and removes punctuation and whitespace. It does not score punctuation, speaker attribution, or timestamps. A negative `cerChange` means fewer character edits against the reference. The script never sends data to a service.

To collect matching excerpts with the CLI, use the same input, `--start`, `--duration`, and `--language` for each version. Save separate output paths. Review the recognized words against the recording before accepting a change based only on fluent text or a larger character count.
