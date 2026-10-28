<h1 align="center">transcribe</h1>

<p align="center">
  English · <a href="README.zh-tw.md">繁體中文</a>
</p>

transcribe is a local command-line tool for audio transcription and speaker diarization on Apple silicon.

Turn recordings into readable transcripts, JSON for agents and scripts, or SRT subtitles. The CLI uses Bun and Effect, with Qwen3-ASR for speech recognition and Nemotron for speaker diarization through MLX.

Preparation downloads dependencies and model files. Transcription uses local snapshots with Hugging Face and Transformers offline flags. Speaker labels distinguish voices within a run; they do not identify people by name.

## Getting started

Requires an Apple Silicon Mac running macOS 14 or newer, Bun, uv, and FFmpeg. With Bun and Homebrew already installed, run these commands from this source directory:

```bash
brew install uv ffmpeg
bun install --frozen-lockfile
bun src/cli.ts setup
bun src/cli.ts doctor
```

`setup` installs the pinned Python environment and downloads the model snapshots. Run it before your first transcription. `doctor` checks the installed dependencies and model files.

Transcribe a recording into a text file:

```bash
bun src/cli.ts run "recording.m4a" --format text --output transcript.txt
```

For a shorter first run, transcribe two minutes:

```bash
bun src/cli.ts run "recording.m4a" --duration 120 --format text
```

JSON is the default format. Use `--format srt` for subtitles. Progress goes to stderr; the transcript goes to stdout or the path supplied with `--output`. Existing output files require `--force` to replace.

Chinese transcript text defaults to Taiwan Traditional Chinese in every format, using local OpenCC conversion after recognition. Use `--chinese-script original` to keep the model's original text. This setting does not translate other languages or change speaker labels and timestamps.

See the [command-line guide](docs/cli.md) for examples, options, configuration, and troubleshooting.

## Documentation

- [Command-line guide](docs/cli.md): output formats, partial recordings, automation, and configuration.
- [Design](docs/DESIGN.md): model selection and the transcription pipeline.
- [Verification](docs/VERIFICATION.md): the real-recording test, results, and accuracy limitations.
- [Model pins](model-pins.json): exact model repositories and revisions.

Up to eight speaker labels are supported. Overlapping voices remain unattributed, timestamps describe speaker turns rather than individual words, and intervals without recognized text are reported in the JSON warnings. Review important transcripts against the recording.

## Development

Install the JavaScript dependencies and run the checks:

```bash
bun install --frozen-lockfile
bun run check
bun run test
bun run build
```

Tests cover CLI behavior, FFmpeg conversion through a protocol fixture, schema validation, output protection, cancellation, and speaker interval partitioning. Real model inference is verified separately.

The build produces `dist/cli.js`, which runs with Bun. Keep the source project, Python worker, model pins, and `.venv` together; the JavaScript bundle is not a standalone executable.
