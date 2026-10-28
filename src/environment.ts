import { access, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { Schema } from "effect";
import { appError } from "./errors";
import { runProcess } from "./process";
import { getRuntimeDirectory } from "./runtime";
import { getDataDirectory } from "./transcriber";

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

export type ModelState = Schema.Schema.Type<typeof ModelsLockSchema>;

export type Diagnostic = {
  readonly name: string;
  readonly ok: boolean;
  readonly detail: string;
};

function runtimeEnvironment(): Record<string, string | undefined> {
  const dataDirectory = getDataDirectory();
  return {
    ...process.env,
    HF_HOME: process.env["HF_HOME"] ?? join(dataDirectory, "huggingface"),
    HF_HUB_DISABLE_TELEMETRY: "1",
  };
}

export async function loadModelState(): Promise<ModelState | undefined> {
  try {
    const parsed: unknown = JSON.parse(
      await readFile(join(getDataDirectory(), "models.lock.json"), "utf8"),
    );
    return Schema.decodeUnknownSync(ModelsLockSchema)(parsed);
  } catch {
    return undefined;
  }
}

export async function setupModels(signal: AbortSignal, quiet: boolean): Promise<void> {
  const projectRoot = getRuntimeDirectory();
  const uv = Bun.which("uv");
  if (uv === null) throw appError("DependencyError", "uv is required; install uv and retry", 69);
  const dataDirectory = getDataDirectory();
  const pythonPath = join(projectRoot, ".venv", "bin", "python");
  const workerPath = join(projectRoot, "python", "worker.py");
  const manifestPath = join(dataDirectory, "models.lock.json");
  const pinsPath = join(projectRoot, "model-pins.json");
  const env = runtimeEnvironment();

  if (!quiet) process.stderr.write("transcribe: installing the pinned MLX Audio environment\n");
  const setupStderr = quiet
    ? {}
    : {
        onStderr: (chunk: string): void => {
          process.stderr.write(chunk);
        },
      };
  const dependencyInstall = await runProcess(uv, ["sync", "--project", projectRoot, "--frozen"], {
    signal,
    env,
    ...setupStderr,
  });
  if (dependencyInstall.exitCode !== 0) {
    throw appError(
      "SetupError",
      `uv could not install mlx-audio==0.6.2: ${(dependencyInstall.stderr || dependencyInstall.stdout).trim()}`,
    );
  }
  await access(pythonPath).catch(() => {
    throw appError("SetupError", `uv sync did not create ${pythonPath}`);
  });
  if (!quiet) process.stderr.write("transcribe: downloading pinned model snapshots\n");
  const download = await runProcess(pythonPath, [workerPath, "setup", manifestPath, pinsPath], {
    signal,
    env,
    ...setupStderr,
  });
  if (download.exitCode !== 0) {
    throw appError(
      "SetupError",
      `model download failed: ${(download.stderr || download.stdout).trim()}`,
    );
  }
}

export async function inspectModels(): Promise<ReadonlyArray<Diagnostic>> {
  const lockPath = join(getDataDirectory(), "models.lock.json");
  const state = await loadModelState();
  if (state === undefined) {
    return [
      {
        name: "model manifest",
        ok: false,
        detail: `missing or invalid at ${lockPath}; run transcribe setup`,
      },
    ];
  }
  const diagnostics: Diagnostic[] = [];
  for (const [name, model] of Object.entries(state.models)) {
    const exists = await stat(model.path)
      .then((value) => value.isDirectory())
      .catch(() => false);
    diagnostics.push({
      name,
      ok: exists,
      detail: exists
        ? `${model.id}@${model.revision}`
        : `${model.id}@${model.revision} snapshot missing; run transcribe setup`,
    });
  }
  return diagnostics;
}

export async function inspectEnvironment(): Promise<ReadonlyArray<Diagnostic>> {
  const projectRoot = getRuntimeDirectory();
  const diagnostics: Diagnostic[] = [
    { name: "Bun", ok: true, detail: Bun.version },
    {
      name: "ffmpeg",
      ok: Bun.which("ffmpeg") !== null,
      detail: Bun.which("ffmpeg") ?? "not found",
    },
    {
      name: "ffprobe",
      ok: Bun.which("ffprobe") !== null,
      detail: Bun.which("ffprobe") ?? "not found",
    },
    { name: "uv", ok: Bun.which("uv") !== null, detail: Bun.which("uv") ?? "not found" },
  ];
  const pythonPath = join(projectRoot, ".venv", "bin", "python");
  try {
    await access(pythonPath);
    const result = await runProcess(
      pythonPath,
      ["-c", "import importlib.metadata; print(importlib.metadata.version('mlx-audio'))"],
      { env: { ...runtimeEnvironment(), HF_HUB_OFFLINE: "1", TRANSFORMERS_OFFLINE: "1" } },
    );
    const version = result.stdout.trim();
    diagnostics.push({
      name: "mlx-audio",
      ok: result.exitCode === 0 && version === "0.6.2",
      detail: result.exitCode === 0 ? version : "not installed in .venv; run transcribe setup",
    });
  } catch {
    diagnostics.push({
      name: "project Python",
      ok: false,
      detail: "missing .venv; run transcribe setup",
    });
  }
  diagnostics.push(...(await inspectModels()));
  return diagnostics;
}
