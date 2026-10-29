import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { link, mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Effect, Fiber } from "effect";
import { transcribeEffect } from "../src/transcriber";

const root = resolve(import.meta.dir, "..");
let directory = "";
let inputPath = "";
let outputPath = "";
let workerPath = "";
let modelHome = "";

async function writeWave(path: string, durationSeconds = 1.2): Promise<void> {
  const sampleRate = 16000;
  const data = Buffer.alloc(Math.floor(sampleRate * durationSeconds) * 2);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  await writeFile(path, Buffer.concat([header, data]));
}

async function prepare(): Promise<void> {
  directory = await mkdtemp(join(tmpdir(), "local-transcribe-test-"));
  inputPath = join(directory, "sample.wav");
  outputPath = join(directory, "result.json");
  workerPath = join(directory, "fake-worker.py");
  modelHome = join(directory, "models");
  await writeWave(inputPath);
  await mkdir(join(modelHome, "asr"), { recursive: true });
  await mkdir(join(modelHome, "diarization"), { recursive: true });
  await mkdir(join(modelHome, "alignment"), { recursive: true });
  await writeFile(
    join(modelHome, "models.lock.json"),
    JSON.stringify({
      schemaVersion: 1,
      models: {
        asr: {
          id: "mlx-community/Qwen3-ASR-1.7B-8bit",
          revision: "a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
          path: join(modelHome, "asr"),
        },
        alignment: {
          id: "mlx-community/Qwen3-ForcedAligner-0.6B-8bit",
          revision: "0e1a68e91d815300c7c9754b2a7639378b23db15",
          path: join(modelHome, "alignment"),
        },
        diarization: {
          id: "mlx-community/Nemotron-3-Diarization-8bit",
          revision: "dd8b8ce3d69a80540a7e50150e2beed1c34fee37",
          path: join(modelHome, "diarization"),
        },
      },
    }),
  );
  await writeFile(
    workerPath,
    [
      "import json, os, sys, time",
      "request = json.load(sys.stdin)",
      "ready = os.environ.get('FAKE_WORKER_READY')",
      "if ready: open(ready, 'w').write(str(os.getpid()))",
      "time.sleep(float(os.environ.get('FAKE_WORKER_SLEEP', '0'))) ",
      "start = request['source_offset'] + 0.05",
      "end = min(request['source_offset'] + request['selected_duration'], start + 0.2)",
      "transcript = {",
      " 'schemaVersion': 1,",
      " 'source': {'path': request['source_path'], 'duration': request['source_duration'], 'selection': {'start': request['source_offset'], 'end': request['source_offset'] + request['selected_duration']}},",
      " 'models': {",
      "  'asr': 'mlx-community/Qwen3-ASR-1.7B-8bit@a8379a2e2f9e313c9292cdf1af4055ab56d50d55',",
      "  'alignment': os.environ.get('FAKE_ALIGNMENT_MODEL', 'mlx-community/Qwen3-ForcedAligner-0.6B-8bit@0e1a68e91d815300c7c9754b2a7639378b23db15'),",
      "  'diarization': 'mlx-community/Nemotron-3-Diarization-8bit@dd8b8ce3d69a80540a7e50150e2beed1c34fee37',",
      " },",
      " 'timing': 'forced-aligned',",
      " 'segments': [{'start': start, 'end': end, 'text': os.environ.get('FAKE_WORKER_TEXT', 'fixture transcript'), 'speaker': 'speaker_0', 'activeSpeakers': ['speaker_0']}],",
      " 'warnings': [],",
      "}",
      "json.dump({'ok': True, 'transcript': transcript}, sys.stdout)",
      "print()",
    ].join("\n"),
  );
}

function run(args: ReadonlyArray<string>, extraEnv: Record<string, string> = {}) {
  const childResult = Bun.spawnSync({
    cmd: [process.execPath, "src/cli.ts", ...args],
    cwd: root,
    env: {
      ...globalThis.process.env,
      NODE_ENV: "test",
      TRANSCRIBE_HOME: modelHome,
      TRANSCRIBE_TEST_WORKER: workerPath,
      HF_HOME: join(directory, "huggingface"),
      TMPDIR: directory,
      ...extraEnv,
    },
    stdout: "pipe",
    stderr: "pipe",
    timeout: 30000,
  });
  return {
    exitCode: childResult.exitCode,
    stdout: Buffer.from(childResult.stdout).toString("utf8"),
    stderr: Buffer.from(childResult.stderr).toString("utf8"),
  };
}

beforeEach(prepare);
afterEach(async () => {
  if (directory) await rm(directory, { recursive: true, force: true });
});

describe("transcribe run CLI", () => {
  test("defaults to Traditional Chinese and can preserve original text", () => {
    const env = { FAKE_WORKER_TEXT: "这是录音，头发。Hello." };
    const converted = run(["run", inputPath, "--quiet"], env);
    expect(converted.exitCode).toBe(0);
    expect(JSON.parse(converted.stdout).segments[0].text).toBe("這是錄音，頭髮。Hello.");
    const original = run(["run", inputPath, "--quiet", "--chinese-script", "original"], env);
    expect(original.exitCode).toBe(0);
    expect(JSON.parse(original.stdout).segments[0].text).toBe("这是录音，头发。Hello.");
  });

  test("prints help cleanly when called without arguments", () => {
    const result = run([]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("Transcribe local audio with speaker diarization");
    expect(result.stderr).toBe("");
  });

  test("runs through FFmpeg and the worker protocol while preserving source offsets", () => {
    const result = run([
      "run",
      inputPath,
      "--start",
      "0.5",
      "--duration",
      "0.25",
      "--json",
      "--quiet",
    ]);
    expect(result.exitCode).toBe(0);
    expect(result.stderr).toBe("");
    expect(JSON.parse(result.stdout)).toMatchObject({
      schemaVersion: 1,
      source: { path: inputPath, selection: { start: 0.5, end: 0.75 } },
      timing: "forced-aligned",
      segments: [{ start: 0.55, text: "fixture transcript", speaker: "speaker_0" }],
    });
  });

  test("protects an existing output and replaces it only with --force", async () => {
    const first = run(["run", inputPath, "--output", outputPath, "--quiet"]);
    expect(first.exitCode).toBe(0);
    const original = await Bun.file(outputPath).text();
    const second = run(["run", inputPath, "--output", outputPath, "--quiet"]);
    expect(second.exitCode).toBe(1);
    expect(JSON.parse(second.stderr)).toMatchObject({ error: { type: "OutputExists" } });
    expect(await Bun.file(outputPath).text()).toBe(original);
    const replacement = run(["run", inputPath, "--output", outputPath, "--force", "--quiet"]);
    expect(replacement.exitCode).toBe(0);
    expect(JSON.parse(await Bun.file(outputPath).text()).segments[0].text).toBe(
      "fixture transcript",
    );
  });

  test("reports input errors as a machine-readable stderr object", () => {
    const result = run(["run", join(directory, "missing.wav"), "--quiet"]);
    expect(result.exitCode).toBe(2);
    expect(result.stdout).toBe("");
    expect(JSON.parse(result.stderr)).toMatchObject({ error: { type: "InputError", exitCode: 2 } });
  });

  test("rejects trailing characters in numeric options", () => {
    const result = run(["run", inputPath, "--start", "0.5seconds", "--quiet"]);
    expect(result.exitCode).toBe(2);
    expect(result.stdout).toBe("");
    expect(JSON.parse(result.stderr)).toMatchObject({ error: { type: "InputError" } });
  });

  test("refuses an output hard link to the source even with --force", async () => {
    const alias = join(directory, "source-alias.wav");
    await link(inputPath, alias);
    const result = run(["run", inputPath, "--output", alias, "--force", "--quiet"]);
    expect(result.exitCode).toBe(2);
    expect(JSON.parse(result.stderr)).toMatchObject({ error: { type: "InputError" } });
  });

  test("accepts --output - as stdout", () => {
    const result = run(["run", inputPath, "--output", "-", "--quiet"]);
    expect(result.exitCode).toBe(0);
    expect(JSON.parse(result.stdout).segments[0].text).toBe("fixture transcript");
  });

  test("terminates the worker and removes temporary audio on Ctrl-C", async () => {
    const readyPath = join(directory, "worker-ready");
    const child = Bun.spawn([process.execPath, "src/cli.ts", "run", inputPath, "--quiet"], {
      cwd: root,
      env: {
        ...globalThis.process.env,
        NODE_ENV: "test",
        TRANSCRIBE_HOME: modelHome,
        TRANSCRIBE_TEST_WORKER: workerPath,
        TMPDIR: directory,
        FAKE_WORKER_SLEEP: "30",
        FAKE_WORKER_READY: readyPath,
      },
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
    });
    const startedAt = Date.now();
    while (!(await Bun.file(readyPath).exists())) {
      if (Date.now() - startedAt > 10000) throw new Error("fake worker did not start in time");
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 20));
    }
    const workerPid = Number(await Bun.file(readyPath).text());
    child.kill("SIGINT");
    const exitCode = await child.exited;
    const files = await readdir(directory);
    expect(exitCode).toBe(130);
    expect(files.some((name) => name.startsWith("transcribe-"))).toBe(false);
    let workerAlive = true;
    try {
      process.kill(workerPid, 0);
    } catch (cause) {
      if (!(cause instanceof Error) || !("code" in cause) || cause.code !== "ESRCH") throw cause;
      workerAlive = false;
    }
    expect(workerAlive).toBe(false);
  });

  test("Effect fiber interruption reaches the worker and releases its temporary audio", async () => {
    const readyPath = join(directory, "effect-worker-ready");
    const keys = [
      "NODE_ENV",
      "TRANSCRIBE_HOME",
      "TRANSCRIBE_TEST_WORKER",
      "TMPDIR",
      "FAKE_WORKER_SLEEP",
      "FAKE_WORKER_READY",
    ];
    const previous = new Map(keys.map((key) => [key, process.env[key]]));
    process.env["NODE_ENV"] = "test";
    process.env["TRANSCRIBE_HOME"] = modelHome;
    process.env["TRANSCRIBE_TEST_WORKER"] = workerPath;
    process.env["TMPDIR"] = directory;
    process.env["FAKE_WORKER_SLEEP"] = "30";
    process.env["FAKE_WORKER_READY"] = readyPath;
    try {
      const fiber = Effect.runFork(
        transcribeEffect({
          inputPath,
          startSeconds: 0,
          durationSeconds: undefined,
          language: undefined,
          timeoutMs: 30000,
          signal: new AbortController().signal,
          quiet: true,
        }),
      );
      const startedAt = Date.now();
      while (!(await Bun.file(readyPath).exists())) {
        if (Date.now() - startedAt > 10000) throw new Error("fake worker did not start in time");
        await new Promise((resolveDelay) => setTimeout(resolveDelay, 20));
      }
      const workerPid = Number(await Bun.file(readyPath).text());
      await Effect.runPromise(Fiber.interrupt(fiber));
      let workerAlive = true;
      const cleanupStartedAt = Date.now();
      while (workerAlive && Date.now() - cleanupStartedAt < 5000) {
        try {
          process.kill(workerPid, 0);
        } catch (cause) {
          if (!(cause instanceof Error) || !("code" in cause) || cause.code !== "ESRCH")
            throw cause;
          workerAlive = false;
        }
        if (workerAlive) await new Promise((resolveDelay) => setTimeout(resolveDelay, 20));
      }
      expect(workerAlive).toBe(false);
      const files = await readdir(directory);
      expect(files.some((name) => name.startsWith("transcribe-"))).toBe(false);
    } finally {
      for (const [key, value] of previous) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    }
  });
});
