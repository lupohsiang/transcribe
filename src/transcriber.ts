import { access, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Context, Effect, Layer, Schema } from "effect";
import { type AppError, AppFailure, appError } from "./errors";
import { runProcess } from "./process";
import { decodeTranscript, type Transcript } from "./transcript";

const ModelSnapshotSchema = Schema.Struct({
  id: Schema.String,
  revision: Schema.String,
  path: Schema.String,
});

const ModelsLockSchema = Schema.Struct({
  schemaVersion: Schema.Literal(1),
  models: Schema.Struct({
    asr: ModelSnapshotSchema,
    diarization: ModelSnapshotSchema,
  }),
});

const WorkerResponseSchema = Schema.Union([
  Schema.Struct({ ok: Schema.Literal(true), transcript: Schema.Unknown }),
  Schema.Struct({
    ok: Schema.Literal(false),
    error: Schema.Struct({ code: Schema.String, message: Schema.String }),
  }),
]);

export type TranscriptionRequest = {
  readonly inputPath: string;
  readonly startSeconds: number;
  readonly durationSeconds: number | undefined;
  readonly language: string | undefined;
  readonly timeoutMs: number;
  readonly signal: AbortSignal;
  readonly quiet: boolean;
};

type WorkerInput = {
  readonly audio_path: string;
  readonly source_path: string;
  readonly source_duration: number;
  readonly source_offset: number;
  readonly selected_duration: number;
  readonly language: string | null;
  readonly manifest_path: string;
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

  const sourcePath = resolve(request.inputPath);
  const sourceInfo = await stat(sourcePath).catch(() => undefined);
  if (sourceInfo === undefined || !sourceInfo.isFile()) {
    throw appError("InputError", `audio file not found: ${request.inputPath}`);
  }

  const projectRoot = resolve(import.meta.dir, "..");
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
    throw appError("SetupError", `model setup manifest is invalid: ${String(cause)}`);
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

  const temporaryDirectory = await mkdtemp(join(tmpdir(), "transcribe-"));
  const audioPath = join(temporaryDirectory, "source.wav");
  try {
    const ffmpegArgs = ["-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", sourcePath];
    if (request.startSeconds > 0) ffmpegArgs.push("-ss", String(request.startSeconds));
    if (request.durationSeconds !== undefined) ffmpegArgs.push("-t", String(selectedDuration));
    ffmpegArgs.push(
      "-map",
      "0:a:0",
      "-vn",
      "-sn",
      "-dn",
      "-ac",
      "1",
      "-ar",
      "16000",
      "-c:a",
      "pcm_s16le",
      "-f",
      "wav",
      audioPath,
    );
    const conversion = await runProcess(ffmpeg, ffmpegArgs, {
      signal: timedSignal,
      ...stderrOption,
    });
    if (conversion.exitCode !== 0) {
      throw appError(
        "MediaError",
        `could not decode audio: ${tail(conversion.stderr) || sourcePath}`,
      );
    }

    const workerPath =
      process.env["NODE_ENV"] === "test" && process.env["TRANSCRIBE_TEST_WORKER"] !== undefined
        ? resolve(process.env["TRANSCRIBE_TEST_WORKER"])
        : join(projectRoot, "python", "worker.py");
    await requirePath(workerPath, "the MLX worker");
    const workerInput: WorkerInput = {
      audio_path: audioPath,
      source_path: sourcePath,
      source_duration: sourceDuration,
      source_offset: request.startSeconds,
      selected_duration: selectedDuration,
      language: request.language ?? null,
      manifest_path: manifestPath,
    };
    const worker = await runProcess(pythonPath, [workerPath], {
      stdin: `${JSON.stringify(workerInput)}\n`,
      signal: timedSignal,
      env: processEnvironment,
      ...stderrOption,
    });
    let response: Schema.Schema.Type<typeof WorkerResponseSchema>;
    try {
      response = Schema.decodeUnknownSync(WorkerResponseSchema)(JSON.parse(worker.stdout));
    } catch (cause) {
      if (worker.exitCode !== 0) {
        throw appError(
          "WorkerError",
          `MLX worker failed: ${tail(worker.stderr) || `exit code ${worker.exitCode}`}`,
        );
      }
      throw appError("WorkerError", `MLX worker returned an invalid response: ${String(cause)}`);
    }
    if (!response.ok) {
      const tag = response.error.code === "MODEL_MISSING" ? "ModelError" : "WorkerError";
      throw appError(tag, response.error.message);
    }
    if (worker.exitCode !== 0) {
      throw appError(
        "WorkerError",
        `MLX worker failed: ${tail(worker.stderr) || `exit code ${worker.exitCode}`}`,
      );
    }

    let transcript: Transcript;
    try {
      transcript = decodeTranscript(response.transcript);
    } catch (cause) {
      throw appError(
        "TranscriptError",
        `MLX worker returned an invalid transcript: ${String(cause)}`,
      );
    }
    const expectedAsr = `${lock.models.asr.id}@${lock.models.asr.revision}`;
    const expectedDiarization = `${lock.models.diarization.id}@${lock.models.diarization.revision}`;
    if (
      transcript.models.asr !== expectedAsr ||
      transcript.models.diarization !== expectedDiarization
    ) {
      throw appError(
        "TranscriptError",
        "worker model revisions do not match the installed model manifest",
      );
    }
    return transcript;
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
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
