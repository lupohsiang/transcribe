import { access, readFile, realpath, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { Context, Effect, Layer, Schema } from "effect";
import { type AppError, AppFailure, appError } from "./errors";
import { ModelsLockSchema } from "./models";
import { runProcess } from "./process";
import { getRuntimeDirectory } from "./runtime";
import { decodeTranscript, type Transcript, type TranscriptSegment } from "./transcript";

const WorkerRecordSchema = Schema.Union([
  Schema.Struct({
    type: Schema.Literal("header"),
    protocol: Schema.Literal(1),
    transcript: Schema.Unknown,
  }),
  Schema.Struct({ type: Schema.Literal("segment"), segment: Schema.Unknown }),
  Schema.Struct({
    type: Schema.Literal("done"),
    count: Schema.Number,
    warnings: Schema.Array(Schema.String),
  }),
  Schema.Struct({
    type: Schema.Literal("error"),
    error: Schema.Struct({
      code: Schema.Union([
        Schema.Literal("WORKER_ERROR"),
        Schema.Literal("MODEL_MISSING"),
        Schema.Literal("MEDIA_ERROR"),
      ]),
      message: Schema.String,
    }),
  }),
]);

const MAX_WORKER_LINE_BYTES = 1024 * 1024;

export type TranscriptionRequest = {
  readonly inputPath: string;
  readonly startSeconds: number;
  readonly durationSeconds: number | undefined;
  readonly language: string | undefined;
  readonly timeoutMs: number;
  readonly signal: AbortSignal;
  readonly quiet: boolean;
  readonly onSegment?: (segment: TranscriptSegment) => void | Promise<void>;
};

type WorkerInput = {
  readonly source_path: string;
  readonly source_duration: number;
  readonly source_offset: number;
  readonly selected_duration: number;
  readonly language: string | null;
  readonly manifest_path: string;
  readonly ffmpeg_path: string;
};

type WorkerStreamState =
  | { readonly kind: "header" }
  | {
      readonly kind: "segments";
      readonly header: Transcript;
      readonly segments: TranscriptSegment[];
    }
  | {
      readonly kind: "complete";
      readonly header: Transcript;
      readonly segments: ReadonlyArray<TranscriptSegment>;
      readonly warnings: ReadonlyArray<string>;
    };

type TranscriberShape = {
  readonly run: (request: TranscriptionRequest) => Effect.Effect<Transcript, AppError>;
};

export class Transcriber extends Context.Service<Transcriber, TranscriberShape>()(
  "local-transcribe/Transcriber",
) {}

function failure(cause: unknown): AppError {
  if (cause instanceof AppFailure) return cause.error;
  return {
    _tag: "InternalError",
    message: `unexpected internal error: ${cause instanceof Error ? cause.message : String(cause)}`,
    exitCode: 70,
  };
}

function tail(text: string, limit = 2500): string {
  return text.length <= limit ? text.trim() : text.slice(-limit).trim();
}

class WorkerStream {
  private buffer = "";
  private state: WorkerStreamState = { kind: "header" };

  constructor(
    private readonly expected: {
      readonly sourcePath: string;
      readonly sourceDuration: number;
      readonly sourceOffset: number;
      readonly selectedDuration: number;
      readonly asrModel: string;
      readonly diarizationModel: string;
      readonly alignmentModel: string;
    },
    private readonly onSegment?: (segment: TranscriptSegment) => void | Promise<void>,
  ) {}

  async push(chunk: string): Promise<void> {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      let line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);
      if (line.length === 0) throw appError("WorkerError", "MLX worker emitted an empty record");
      if (Buffer.byteLength(line, "utf8") > MAX_WORKER_LINE_BYTES) {
        throw appError("WorkerError", "MLX worker record exceeds the 1 MiB limit");
      }
      await this.accept(line);
      newline = this.buffer.indexOf("\n");
    }
    if (Buffer.byteLength(this.buffer, "utf8") > MAX_WORKER_LINE_BYTES) {
      throw appError("WorkerError", "MLX worker record exceeds the 1 MiB limit");
    }
  }

  finish(): Transcript {
    if (this.buffer.length > 0) {
      throw appError("WorkerError", "MLX worker ended with a truncated record");
    }
    if (this.state.kind === "header") {
      throw appError("WorkerError", "MLX worker ended before sending a header");
    }
    if (this.state.kind === "segments") {
      throw appError("WorkerError", "MLX worker ended before sending a done record");
    }
    try {
      return decodeTranscript({
        ...this.state.header,
        segments: this.state.segments,
        warnings: this.state.warnings,
      });
    } catch (cause) {
      throw appError(
        "TranscriptError",
        `MLX worker returned an invalid transcript: ${String(cause)}`,
      );
    }
  }

  private async accept(line: string): Promise<void> {
    let record: Schema.Schema.Type<typeof WorkerRecordSchema>;
    try {
      record = Schema.decodeUnknownSync(WorkerRecordSchema)(JSON.parse(line));
    } catch {
      throw appError("WorkerError", "MLX worker emitted an invalid NDJSON record");
    }
    if (this.state.kind === "complete") {
      throw appError("WorkerError", "MLX worker emitted a record after done");
    }
    if (record.type === "error") {
      const tag =
        record.error.code === "MODEL_MISSING"
          ? "ModelError"
          : record.error.code === "MEDIA_ERROR"
            ? "MediaError"
            : "WorkerError";
      throw appError(tag, record.error.message);
    }
    if (record.type === "header") {
      if (this.state.kind !== "header") {
        throw appError("WorkerError", "MLX worker emitted more than one header");
      }
      let header: Transcript;
      try {
        header = decodeTranscript(record.transcript);
      } catch (cause) {
        throw appError(
          "TranscriptError",
          `MLX worker returned an invalid header: ${String(cause)}`,
        );
      }
      const expectedSelectionEnd = Math.min(
        this.expected.sourceDuration,
        this.expected.sourceOffset + this.expected.selectedDuration,
      );
      if (
        header.timing !== "forced-aligned" ||
        header.segments.length !== 0 ||
        header.warnings.length !== 0 ||
        header.source.path !== this.expected.sourcePath ||
        header.source.duration !== this.expected.sourceDuration ||
        header.source.selection.start !== this.expected.sourceOffset ||
        header.source.selection.end !== expectedSelectionEnd ||
        header.models.asr !== this.expected.asrModel ||
        header.models.diarization !== this.expected.diarizationModel ||
        header.models.alignment !== this.expected.alignmentModel
      ) {
        throw appError(
          "TranscriptError",
          "worker header does not match the selected source or installed model manifest",
        );
      }
      this.state = { kind: "segments", header, segments: [] };
      return;
    }
    if (record.type === "segment") {
      if (this.state.kind !== "segments") {
        throw appError("WorkerError", "MLX worker emitted a segment before its header");
      }
      let segment: TranscriptSegment;
      try {
        const decoded = decodeTranscript({
          ...this.state.header,
          segments: [record.segment],
        }).segments[0];
        if (decoded === undefined) throw new Error("segment was lost during validation");
        segment = decoded;
      } catch (cause) {
        throw appError(
          "TranscriptError",
          `MLX worker returned an invalid segment: ${String(cause)}`,
        );
      }
      const previous = this.state.segments[this.state.segments.length - 1];
      if (previous !== undefined && segment.start < previous.end) {
        throw appError("TranscriptError", "worker segments must be ordered and non-overlapping");
      }
      await this.onSegment?.(segment);
      this.state.segments.push(segment);
      return;
    }
    if (this.state.kind !== "segments") {
      throw appError("WorkerError", "MLX worker emitted done before its header");
    }
    if (!Number.isSafeInteger(record.count) || record.count < 0) {
      throw appError("WorkerError", "MLX worker done count must be a non-negative integer");
    }
    if (record.count !== this.state.segments.length) {
      throw appError("WorkerError", "MLX worker done count does not match the streamed segments");
    }
    this.state = {
      kind: "complete",
      header: this.state.header,
      segments: this.state.segments,
      warnings: record.warnings,
    };
  }
}

async function requirePath(path: string, label: string): Promise<void> {
  try {
    await access(path);
  } catch {
    throw appError(
      "DependencyError",
      `${label} was not found at ${path}; run transcribe setup`,
      69,
    );
  }
}

async function runTranscription(request: TranscriptionRequest): Promise<Transcript> {
  if (!Number.isFinite(request.startSeconds) || request.startSeconds < 0) {
    throw appError("InputError", "--start must be a finite number greater than or equal to 0");
  }
  if (
    request.durationSeconds !== undefined &&
    (!Number.isFinite(request.durationSeconds) || request.durationSeconds <= 0)
  ) {
    throw appError("InputError", "--duration must be a finite number greater than 0");
  }
  if (!Number.isFinite(request.timeoutMs) || request.timeoutMs <= 0) {
    throw appError("InputError", "--timeout must be a finite number greater than 0");
  }

  const resolvedInputPath = resolve(request.inputPath);
  const sourceInfo = await stat(resolvedInputPath).catch(() => undefined);
  if (sourceInfo === undefined || !sourceInfo.isFile()) {
    throw appError("InputError", `audio file not found: ${request.inputPath}`);
  }
  const sourcePath = await realpath(resolvedInputPath).catch(() => {
    throw appError("InputError", `audio file not found: ${request.inputPath}`);
  });

  const projectRoot = getRuntimeDirectory();
  const dataDirectory = getDataDirectory();
  const manifestPath = join(dataDirectory, "models.lock.json");
  const pythonPath = await getPythonPath(projectRoot);
  const ffmpeg = Bun.which("ffmpeg");
  const ffprobe = Bun.which("ffprobe");
  if (ffmpeg === null || ffprobe === null) {
    throw appError(
      "DependencyError",
      "ffmpeg and ffprobe are required; install FFmpeg and retry",
      69,
    );
  }
  await requirePath(pythonPath, "the project Python environment");
  await requirePath(manifestPath, "the model setup manifest");

  let lock: Schema.Schema.Type<typeof ModelsLockSchema>;
  try {
    lock = Schema.decodeUnknownSync(ModelsLockSchema)(
      JSON.parse(await readFile(manifestPath, "utf8")),
    );
  } catch (cause) {
    throw appError(
      "SetupError",
      `model setup manifest is invalid; run transcribe setup: ${String(cause)}`,
    );
  }
  for (const [name, model] of Object.entries(lock.models)) {
    try {
      if (!(await stat(model.path)).isDirectory()) throw new Error("not a directory");
    } catch {
      throw appError("ModelError", `${name} model files are missing; run transcribe setup`);
    }
  }

  const timedSignal = AbortSignal.any([request.signal, AbortSignal.timeout(request.timeoutMs)]);
  const processEnvironment = {
    ...process.env,
    HF_HOME: process.env["HF_HOME"] ?? join(dataDirectory, "huggingface"),
    HF_HUB_OFFLINE: "1",
    TRANSFORMERS_OFFLINE: "1",
    HF_HUB_DISABLE_TELEMETRY: "1",
  };
  const stderrOption = request.quiet
    ? {}
    : {
        onStderr: (chunk: string): void => {
          process.stderr.write(chunk);
        },
      };

  const probe = await runProcess(
    ffprobe,
    [
      "-v",
      "error",
      "-show_entries",
      "format=duration",
      "-of",
      "default=noprint_wrappers=1:nokey=1",
      sourcePath,
    ],
    { signal: timedSignal, ...stderrOption },
  );
  if (probe.exitCode !== 0) {
    throw appError(
      "MediaError",
      `could not read audio metadata: ${tail(probe.stderr) || sourcePath}`,
    );
  }
  const sourceDuration = Number.parseFloat(probe.stdout.trim());
  if (!Number.isFinite(sourceDuration) || sourceDuration <= 0) {
    throw appError("MediaError", "the input has no readable audio duration");
  }
  if (request.startSeconds >= sourceDuration) {
    throw appError(
      "InputError",
      `--start must be less than the source duration (${sourceDuration.toFixed(3)}s)`,
    );
  }
  const selectedDuration = request.durationSeconds ?? sourceDuration - request.startSeconds;
  if (selectedDuration > sourceDuration - request.startSeconds + 0.01) {
    throw appError("InputError", "--duration extends past the end of the source audio");
  }

  const workerPath =
    process.env["NODE_ENV"] === "test" && process.env["TRANSCRIBE_TEST_WORKER"] !== undefined
      ? resolve(process.env["TRANSCRIBE_TEST_WORKER"])
      : join(projectRoot, "python", "worker.py");
  await requirePath(workerPath, "the MLX worker");
  const workerInput: WorkerInput = {
    source_path: sourcePath,
    source_duration: sourceDuration,
    source_offset: request.startSeconds,
    selected_duration: selectedDuration,
    language: request.language ?? null,
    manifest_path: manifestPath,
    ffmpeg_path: resolve(ffmpeg),
  };
  const stream = new WorkerStream(
    {
      sourcePath,
      sourceDuration,
      sourceOffset: request.startSeconds,
      selectedDuration,
      asrModel: `${lock.models.asr.id}@${lock.models.asr.revision}`,
      diarizationModel: `${lock.models.diarization.id}@${lock.models.diarization.revision}`,
      alignmentModel: `${lock.models.alignment.id}@${lock.models.alignment.revision}`,
    },
    request.onSegment,
  );
  const worker = await runProcess(pythonPath, [workerPath], {
    stdin: `${JSON.stringify(workerInput)}\n`,
    signal: timedSignal,
    env: processEnvironment,
    captureStdout: false,
    onStdout: (chunk) => stream.push(chunk),
    ...stderrOption,
  });
  if (worker.exitCode !== 0) {
    throw appError(
      "WorkerError",
      `MLX worker failed: ${tail(worker.stderr) || `exit code ${worker.exitCode}`}`,
    );
  }
  return stream.finish();
}

export function getDataDirectory(): string {
  const override = process.env["TRANSCRIBE_HOME"];
  if (override !== undefined && override.length > 0) return resolve(override);
  if (process.platform === "darwin")
    return join(process.env["HOME"] ?? ".", "Library", "Application Support", "transcribe");
  return join(
    process.env["XDG_DATA_HOME"] ?? join(process.env["HOME"] ?? ".", ".local", "share"),
    "transcribe",
  );
}

export async function getPythonPath(projectRoot: string): Promise<string> {
  const candidate = join(projectRoot, ".venv", "bin", "python");
  try {
    await access(candidate);
    return candidate;
  } catch {
    throw appError(
      "DependencyError",
      `project Python environment is missing at ${candidate}; run transcribe setup`,
      69,
    );
  }
}

export const TranscriberLive = Layer.succeed(Transcriber, {
  run: (request) =>
    Effect.tryPromise({
      try: (fiberSignal) =>
        runTranscription({
          ...request,
          signal: AbortSignal.any([request.signal, fiberSignal]),
        }),
      catch: failure,
    }),
});

export function transcribeEffect(
  request: TranscriptionRequest,
): Effect.Effect<Transcript, AppError> {
  return Effect.gen(function* () {
    const transcriber = yield* Transcriber;
    return yield* transcriber.run(request);
  }).pipe(Effect.provide(TranscriberLive));
}
