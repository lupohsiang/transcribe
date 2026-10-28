# Command-line guide

Using a [native release](install.md)? Replace `bun src/cli.ts` in the examples below with `transcribe`. Bun is only needed when running from source.

[Overview](../README.md) · [繁體中文](cli.zh-tw.md)

Run commands from the source project directory. These examples use `bun src/cli.ts`. After `bun run build`, you can use `bun dist/cli.js` while keeping the rest of the project in place.

## Transcribe a recording

```bash
bun src/cli.ts run "recording.m4a" --format text
bun src/cli.ts run "recording.m4a" --format text --output transcript.txt
bun src/cli.ts run "recording.m4a" --format srt --output subtitles.srt
```

Each `run` command performs inference again. Choosing another format does not reuse an earlier JSON result. Output files are published atomically after transcription finishes. Use `--force` to replace an existing output; the CLI refuses to replace the input audio.

Quote paths that contain spaces. FFmpeg decodes the input into temporary mono 16 kHz PCM audio; the source recording is not modified.

## Select part of a recording

Start at one minute and transcribe the next two minutes:

```bash
bun src/cli.ts run "recording.m4a" --start 60 --duration 120 --format text
```

`--duration` is a length in seconds, not an end time. Timestamps remain absolute offsets into the source file. JSON records the complete source duration and selected range. Speaker labels belong to that selected range; matching labels from separate runs do not identify the same person.

Supply a language hint when needed:

```bash
bun src/cli.ts run "recording.m4a" --language Chinese --format text
```

Without a hint, the ASR model detects the language. `--language` controls recognition, not translation or the output writing system.

## Traditional Chinese output

Chinese transcript text defaults to Taiwan Traditional Chinese in JSON, text, and SRT. Conversion uses bundled OpenCC dictionaries locally after recognition, with the `cn` to `tw` configuration. It converts character forms without enabling Taiwan vocabulary replacement. It does not translate English or change timestamps, speaker labels, file paths, or warnings.

To preserve the model's original text:

```bash
bun src/cli.ts run "recording.m4a" --chinese-script original --format text
```

You can explicitly select the default with `--chinese-script zh-TW`. This conversion does not fix recognition errors or establish word or speaker accuracy.

## Use with agents and scripts

JSON is the default output. Suppress progress and save stdout:

```bash
bun src/cli.ts run "recording.m4a" --quiet > transcript.json
bun src/cli.ts schema > transcript.schema.json
```

`--json` is an alias for `--format json`. `--output -` also writes to stdout. Shell redirection follows your shell's overwrite rules; use `--output transcript.json` when you want the CLI's overwrite protection.

Errors are JSON objects on stderr. Success returns exit code 0; failures return nonzero. Input errors use 2, cancellation uses 130, and timeouts use 124. Without `--quiet`, stderr also contains progress and warning messages, so do not parse the entire stderr stream as one JSON document.

Ctrl-C, SIGTERM, and `--timeout` stop the active subprocess group and clean up temporary audio. The default timeout is six hours. No transcript is published until the run finishes, and interrupted work cannot currently be resumed.

## Commands and options

| Command | Purpose |
| --- | --- |
| `run <file>` | Transcribe audio and label speakers. |
| `setup` | Install the frozen Python environment and download pinned models. |
| `doctor [--json]` | Check dependencies and model snapshot availability. |
| `models [--json]` | Show installed model IDs, revisions, and availability. |
| `schema` | Print the transcript JSON Schema. |

Options for `run`:

| Option | Default | Purpose |
| --- | --- | --- |
| `-f, --format <format>` | `json` | Choose `json`, `text`, or `srt`. |
| `--json` | | Choose JSON output. |
| `-o, --output <path>` | stdout | Write the result to a file; `-` means stdout. |
| `--force` | off | Replace an existing output file. |
| `--start <seconds>` | `0` | Start at this source timestamp. |
| `--duration <seconds>` | Remaining audio | Transcribe this length of audio. |
| `--language <name>` | Automatic | Hint the ASR language, such as `Chinese` or `English`. |
| `--chinese-script <script>` | `zh-TW` | Choose Taiwan Traditional Chinese or `original` model text. |
| `--timeout <seconds>` | `21600` | Limit the run's elapsed time. |
| `-q, --quiet` | off | Suppress progress and worker warnings. |
| `-h, --help` | | Show command help. |

`setup` also accepts `--quiet`. Use `bun src/cli.ts --version` to print the CLI version.

## Transcript format and limitations

Version 1 JSON contains `schemaVersion`, `source`, `models`, `timing`, `segments`, and `warnings`. Each segment contains `start`, `end`, `text`, `speaker`, and `activeSpeakers`. Times are source seconds. `source.selection` records the selected start and end.

For one active voice, `speaker` contains its label. During overlap, `speaker` is `null` and `activeSpeakers` lists the detected voices. The interval is transcribed once; overlapping voices are not separated into isolated audio tracks.

The Effect Schema validates timestamp bounds, segment order, non-overlap, and speaker-field consistency. The exported JSON Schema represents field structure and simple constraints; relational checks are enforced at runtime.

Nemotron processes the selected recording once with continuous speaker state. Adjacent spans with the same active speaker set are combined, and real gaps remain. ASR inputs are bounded to 25 seconds, so long turns can be cut across a sentence. Timestamps describe these turns or slices, not individual words.

Up to eight speaker labels are supported. Labels can drift or split a person's voice on long recordings. Empty ASR results are omitted and counted in JSON warnings; that count does not measure a duration of missing speech. The CLI does not identify people, link voices across recordings, or export separate audio files for each speaker. Review important transcripts against the recording.

## Configuration

The default macOS state directory is `~/Library/Application Support/transcribe`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRANSCRIBE_HOME` | State directory above | Directory containing `models.lock.json`. |
| `HF_HOME` | `$TRANSCRIBE_HOME/huggingface` | Hugging Face cache used during setup. |

The model manifest records absolute snapshot paths. If you move or remove those snapshots, run `setup` again with your chosen settings. Set environment overrides consistently during setup and transcription. Dependencies remain in the project's `.venv`.

`bun install --frozen-lockfile` installs JavaScript dependencies. `setup` uses `uv sync --frozen` and the revisions in `model-pins.json`. These preparation steps need network access. Inference uses local snapshots with offline flags; this is not operating-system network isolation.

## Troubleshooting

Run `bun src/cli.ts doctor` first. Install missing tools, or run `bun src/cli.ts setup` if the Python environment or model snapshots are missing. For command syntax, use `bun src/cli.ts run --help`.

If an output already exists, choose another path or pass `--force`. If a run times out, try a shorter range or increase `--timeout`. A shorter range has its own speaker labels and cannot resume or extend the previous run's identities.
