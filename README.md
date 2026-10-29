<h1 align="center">transcribe</h1>

<p align="center">
  English · <a href="README.zh-tw.md">繁體中文</a>
</p>

transcribe is a local command-line tool for audio transcription and speaker diarization on Apple silicon.

Turn recordings into readable transcripts, JSON for agents and scripts, or SRT subtitles. The CLI uses Bun and Effect, with Qwen3-ASR for speech recognition, Qwen3-ForcedAligner for timestamps, and Nemotron for speaker diarization through MLX.

Preparation downloads dependencies and model files. Transcription uses local snapshots with Hugging Face and Transformers offline flags. Speaker labels distinguish voices within a run; they do not identify people by name.

## Getting started

Requires an Apple Silicon Mac running macOS 14 or newer. Native releases include Bun; install uv and FFmpeg separately. Install the latest release without a GitHub account:

```bash
brew install uv ffmpeg
curl -fsSL https://raw.githubusercontent.com/lupohsiang/transcribe/main/install.sh -o install-transcribe.sh && sh install-transcribe.sh
export PATH="$HOME/.local/bin:$PATH"
transcribe setup
transcribe doctor
transcribe run "$HOME/Downloads/新錄音 4.m4a" --format text --output transcript.txt
```

The installer verifies the archive's SHA-256 checksum and installs into `~/.local/share/transcribe/releases`, with a command symlink in `~/.local/bin`. Add the PATH line to your shell profile if needed. Run the installer again to upgrade, then run `transcribe setup` for the new release. The MLX Python environment and models are prepared separately by `setup`; they are not inside the binary. See [installation and releases](docs/install.md) for curl, version selection, and building native releases.

To run from source instead, install Bun and run these commands from this source directory:

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

Recognition uses continuous audio windows of up to 30 seconds, independently of speaker changes. The forced aligner estimates word or character timestamps before speaker labels are assigned. Speech outside detected speaker activity is kept with an unknown-speaker label. Up to eight speaker labels are supported, and overlapping voices remain unattributed. Review important transcripts against the recording.

Aligned transcription currently supports Chinese, Cantonese, English, German, Spanish, French, Italian, Portuguese, and Russian. Japanese and Korean need tokenizer dependencies that this frozen build does not include. Other ASR languages are unavailable in this pipeline.

Run `setup` again when upgrading from a version without the alignment model. See [transcription quality](docs/QUALITY.md) for the design, limitations, and reference-based evaluation.

## Development

Follow the [coding standards](CODING_STANDARDS.md), including the rule against tautological tests.

Install the JavaScript dependencies and run the checks:

```bash
bun install --frozen-lockfile
bun run check
bun run test
bun run build
bun run build:native
```

Tests cover CLI behavior, FFmpeg conversion through a protocol fixture, schema validation, output protection, cancellation, and speaker interval partitioning. Real model inference is verified separately.

`build` produces `dist/cli.js` for Bun. `build:native` produces an Apple Silicon executable, its Python support files, an install script, and checksums under `dist/release`. Ship the release archive together; moving only the executable leaves its worker files behind.
