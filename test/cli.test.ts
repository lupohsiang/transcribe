import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { type ChildProcess, spawn as spawnChild } from "node:child_process";
import { link, mkdir, mkdtemp, realpath, rm, symlink, writeFile } from "node:fs/promises";
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
  inputPath = await realpath(inputPath);
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
      "assert set(request) == {'source_path', 'source_duration', 'source_offset', 'selected_duration', 'language', 'manifest_path', 'ffmpeg_path'}",
      "assert all(os.path.isabs(request[key]) for key in ('source_path', 'manifest_path', 'ffmpeg_path'))",
      "mode = os.environ.get('FAKE_WORKER_MODE', 'valid')",
      "ready = os.environ.get('FAKE_WORKER_READY')",
      "if ready: open(ready, 'w').write(str(os.getpid()))",
      "time.sleep(float(os.environ.get('FAKE_WORKER_SLEEP', '0'))) ",
      "selection_start = request['source_offset']",
      "selection_end = min(request['source_duration'], selection_start + request['selected_duration'])",
      "header = {",
      " 'schemaVersion': 1,",
      " 'source': {'path': request['source_path'], 'duration': request['source_duration'], 'selection': {'start': selection_start, 'end': selection_end}},",
      " 'models': {",
      "  'asr': 'mlx-community/Qwen3-ASR-1.7B-8bit@a8379a2e2f9e313c9292cdf1af4055ab56d50d55',",
      "  'alignment': os.environ.get('FAKE_ALIGNMENT_MODEL', 'mlx-community/Qwen3-ForcedAligner-0.6B-8bit@0e1a68e91d815300c7c9754b2a7639378b23db15'),",
      "  'diarization': 'mlx-community/Nemotron-3-Diarization-8bit@dd8b8ce3d69a80540a7e50150e2beed1c34fee37',",
      " },",
      " 'timing': 'forced-aligned',",
      " 'segments': [],",
      " 'warnings': [],",
      "}",
      "def emit(record):",
      " data = json.dumps(record, ensure_ascii=False).encode('utf-8')",
      " if os.environ.get('FAKE_WORKER_SPLIT_UTF8') and record.get('type') == 'segment':",
      "  split = data.index('录音'.encode('utf-8')) + 1",
      "  sys.stdout.buffer.write(data[:split]); sys.stdout.flush(); time.sleep(0.02)",
      "  sys.stdout.buffer.write(data[split:] + b'\\n')",
      " else:",
      "  sys.stdout.buffer.write(data + b'\\n')",
      " sys.stdout.flush()",
      "if mode == 'malformed':",
      " sys.stdout.write('{not json}\\n'); sys.stdout.flush(); sys.exit(0)",
      "if mode == 'truncated':",
      ' sys.stdout.write(\'{"type":"header"\'); sys.stdout.flush(); sys.exit(0)',
      "if mode == 'oversized':",
      " sys.stdout.write('x' * (1024 * 1024 + 1) + '\\n'); sys.stdout.flush(); sys.exit(0)",
      "if mode.startswith('error_'):",
      " code = {'error_model': 'MODEL_MISSING', 'error_media': 'MEDIA_ERROR', 'error_worker': 'WORKER_ERROR'}[mode]",
      " emit({'type': 'error', 'error': {'code': code, 'message': 'fixture worker error'}}); sys.exit(0)",
      "if mode == 'bad_header_range': header['source']['selection']['end'] = request['source_duration'] + 1",
      "if mode == 'bad_header_pin': header['models']['asr'] = 'asr@wrong'",
      "if mode == 'bad_header_segments': header['segments'] = [{'start': 0, 'end': 0.1, 'text': 'unexpected', 'speaker': 'speaker_0', 'activeSpeakers': ['speaker_0']}]",
      "if mode == 'segment_before_header':",
      " emit({'type': 'segment', 'segment': {'start': selection_start, 'end': selection_start + 0.1, 'text': 'early', 'speaker': 'speaker_0', 'activeSpeakers': ['speaker_0']}})",
      "if mode == 'segment_before_header': emit({'type': 'header', 'protocol': 1, 'transcript': header}); sys.exit(0)",
      "emit({'type': 'header', 'protocol': 1, 'transcript': header})",
      "if mode == 'second_header': emit({'type': 'header', 'protocol': 1, 'transcript': header}); sys.exit(0)",
      "count = int(os.environ.get('FAKE_WORKER_SEGMENTS', '1'))",
      "text = os.environ.get('FAKE_WORKER_TEXT', 'fixture transcript')",
      "if os.environ.get('FAKE_WORKER_TEXT_BYTES'): text = 'A' * int(os.environ['FAKE_WORKER_TEXT_BYTES'])",
      "segments = []",
      "for index in range(count):",
      " start = selection_start + 0.05 + index * 0.001",
      " end = min(selection_end, start + (0.0005 if count > 1 else 0.2))",
      " segment = {'start': start, 'end': end, 'text': text, 'speaker': 'speaker_0', 'activeSpeakers': ['speaker_0']}",
      " if mode == 'bad_segment_range': segment['end'] = request['source_duration'] + 1",
      " if mode == 'out_of_order' and index == 1: segment['start'] = selection_start + 0.04; segment['end'] = selection_start + 0.045",
      " emit({'type': 'segment', 'segment': segment}); segments.append(segment)",
      " segment_ready = os.environ.get('FAKE_WORKER_SEGMENT_READY')",
      " if segment_ready and index == 0: open(segment_ready, 'w').write('segment emitted')",
      " if mode == 'gated' and index == 0:",
      "  gate = os.environ['FAKE_WORKER_GATE']",
      "  while not os.path.exists(gate): time.sleep(0.01)",
      "if mode == 'missing_done': sys.exit(0)",
      "if mode == 'partial_error': emit({'type': 'error', 'error': {'code': 'WORKER_ERROR', 'message': 'fixture failed after a segment'}}); sys.exit(0)",
      "done_count = count + (1 if mode == 'bad_done_count' else 0)",
      "emit({'type': 'done', 'count': done_count, 'warnings': ['fixture warning']})",
      "if mode == 'trailing': emit({'type': 'done', 'count': count, 'warnings': []})",
    ].join("\n"),
  );
}

function environment(extraEnv: Record<string, string> = {}): Record<string, string | undefined> {
  return {
    ...globalThis.process.env,
    NODE_ENV: "test",
    TRANSCRIBE_HOME: modelHome,
    TRANSCRIBE_TEST_WORKER: workerPath,
    HF_HOME: join(directory, "huggingface"),
    TMPDIR: directory,
    ...extraEnv,
  };
}

function spawnCli(args: ReadonlyArray<string>, extraEnv: Record<string, string> = {}) {
  return Bun.spawn({
    cmd: [process.execPath, "src/cli.ts", ...args],
    cwd: root,
    env: environment(extraEnv),
    stdin: "ignore",
    stdout: "pipe",
    stderr: "pipe",
  });
}

function run(args: ReadonlyArray<string>, extraEnv: Record<string, string> = {}) {
  const childResult = Bun.spawnSync({
    cmd: [process.execPath, "src/cli.ts", ...args],
    cwd: root,
    env: environment(extraEnv),
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

async function waitForFile(path: string): Promise<void> {
  const startedAt = Date.now();
  while (!(await Bun.file(path).exists())) {
    if (Date.now() - startedAt > 10000) throw new Error(`file did not appear: ${path}`);
    await Bun.sleep(20);
  }
}

async function exitWithin(child: ChildProcess, timeoutMs = 10000): Promise<number> {
  return new Promise((resolveExit, rejectExit) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      rejectExit(new Error("CLI did not exit before the test deadline"));
    }, timeoutMs);
    child.once("close", (code, signal) => {
      clearTimeout(timer);
      if (code !== null) resolveExit(code);
      else rejectExit(new Error(`CLI exited from signal ${String(signal)}`));
    });
  });
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (cause) {
    if (cause instanceof Error && "code" in cause && cause.code === "ESRCH") return false;
    throw cause;
  }
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

  test("streams NDJSON segments and atomically exports the completed transcript", async () => {
    const streamPath = join(directory, "streamed.json");
    const result = run(["run", inputPath, "--stream", "--output", streamPath, "--quiet"], {
      FAKE_WORKER_TEXT: "这是录音，头发。Hello.",
      FAKE_WORKER_SPLIT_UTF8: "1",
    });
    expect(result.exitCode).toBe(0);
    const records = result.stdout
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(records).toEqual([
      {
        type: "segment",
        segment: {
          start: 0.05,
          end: 0.25,
          text: "這是錄音，頭髮。Hello.",
          speaker: "speaker_0",
          activeSpeakers: ["speaker_0"],
        },
      },
      {
        type: "complete",
        transcript: {
          schemaVersion: 1,
          source: { path: inputPath, duration: 1.2, selection: { start: 0, end: 1.2 } },
          models: {
            asr: "mlx-community/Qwen3-ASR-1.7B-8bit@a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
            alignment:
              "mlx-community/Qwen3-ForcedAligner-0.6B-8bit@0e1a68e91d815300c7c9754b2a7639378b23db15",
            diarization:
              "mlx-community/Nemotron-3-Diarization-8bit@dd8b8ce3d69a80540a7e50150e2beed1c34fee37",
          },
          timing: "forced-aligned",
          warnings: ["fixture warning"],
        },
        count: 1,
      },
    ]);
    const exported = JSON.parse(await Bun.file(streamPath).text());
    expect(exported.segments).toHaveLength(1);
    expect(exported.segments[0].text).toBe("這是錄音，頭髮。Hello.");
  });

  test("streams text and SRT once with Traditional Chinese and sequential SRT numbers", () => {
    const env = { FAKE_WORKER_SEGMENTS: "2", FAKE_WORKER_TEXT: "这是录音。" };
    const text = run(["run", inputPath, "--stream", "--format", "text", "--quiet"], env);
    expect(text.exitCode).toBe(0);
    expect(text.stdout).toBe(
      "[00:00:00.050 – 00:00:00.051] speaker_0: 這是錄音。\n[00:00:00.051 – 00:00:00.052] speaker_0: 這是錄音。\n",
    );

    const srt = run(["run", inputPath, "--stream", "--format", "srt", "--quiet"], env);
    expect(srt.exitCode).toBe(0);
    expect(srt.stdout.match(/^\d+$/gm)).toEqual(["1", "2"]);
    expect(srt.stdout).toContain("1\n00:00:00,050 --> 00:00:00,051\nspeaker_0: 這是錄音。\n\n");
    expect(srt.stdout).toContain("2\n00:00:00,051 --> 00:00:00,052\nspeaker_0: 這是錄音。\n\n");
  });

  test("streams each accepted segment while the worker is still gated", async () => {
    const readyPath = join(directory, "gated-worker-ready");
    const gatePath = join(directory, "gated-worker-release");
    const child = spawnCli(["run", inputPath, "--stream", "--quiet"], {
      FAKE_WORKER_MODE: "gated",
      FAKE_WORKER_READY: readyPath,
      FAKE_WORKER_GATE: gatePath,
    });
    const stdout = child.stdout;
    if (stdout === null || typeof stdout === "number") throw new Error("CLI stdout is not piped");
    const reader = stdout.getReader();
    const decoder = new TextDecoder();
    let buffered = "";
    const nextLine = async (): Promise<string> => {
      while (true) {
        const newline = buffered.indexOf("\n");
        if (newline >= 0) {
          const line = buffered.slice(0, newline);
          buffered = buffered.slice(newline + 1);
          return line;
        }
        const chunk = await reader.read();
        if (chunk.done) throw new Error("CLI stdout ended before the expected stream record");
        buffered += decoder.decode(chunk.value, { stream: true });
      }
    };
    try {
      const first = JSON.parse(await nextLine());
      expect(first.type).toBe("segment");
      expect(first.segment.text).toBe("fixture transcript");
      const workerPid = Number(await Bun.file(readyPath).text());
      process.kill(workerPid, 0);
      await writeFile(gatePath, "release");
      const complete = JSON.parse(await nextLine());
      expect(complete.type).toBe("complete");
      expect(await child.exited).toBe(0);
    } finally {
      await writeFile(gatePath, "release");
      await reader.cancel();
    }
  });

  test("prints help cleanly when called without arguments", () => {
    const result = run([]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("Transcribe local audio and video with speaker diarization");
    expect(result.stderr).toBe("");
    const commandHelp = run(["run", "--help"]);
    expect(commandHelp.exitCode).toBe(0);
    expect(commandHelp.stdout).toContain("meeting.mp4 --stream --format text");
    expect(commandHelp.stderr).toBe("");
  });

  test("validates the worker protocol while preserving source offsets", () => {
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

  test("accepts a symlinked input and records its canonical path", async () => {
    const alias = join(directory, "source-link.wav");
    await symlink(inputPath, alias);
    const result = run(["run", alias, "--quiet"]);
    expect(result.exitCode).toBe(0);
    expect(JSON.parse(result.stdout).source.path).toBe(inputPath);
  });

  test("accepts --output - as stdout", () => {
    const result = run(["run", inputPath, "--output", "-", "--quiet"]);
    expect(result.exitCode).toBe(0);
    expect(JSON.parse(result.stdout).segments[0].text).toBe("fixture transcript");
  });

  test("uses --output - once for a streamed JSON document", () => {
    const result = run(["run", inputPath, "--stream", "--output", "-", "--quiet"]);
    expect(result.exitCode).toBe(0);
    const records = result.stdout
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(records.map((record) => record.type)).toEqual(["segment", "complete"]);
    expect(records[0].segment.text).toBe("fixture transcript");
  });

  test("does not publish an atomic file or completion record after a partial failure", async () => {
    await writeFile(outputPath, "existing transcript");
    const result = run(
      ["run", inputPath, "--stream", "--output", outputPath, "--force", "--quiet"],
      { FAKE_WORKER_MODE: "partial_error" },
    );
    expect(result.exitCode).toBe(1);
    expect(
      result.stdout
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line).type),
    ).toEqual(["segment"]);
    expect(JSON.parse(result.stderr).error.type).toBe("WorkerError");
    expect(await Bun.file(outputPath).text()).toBe("existing transcript");
  });

  test("rejects malformed and invalid-header worker records", () => {
    const cases: ReadonlyArray<readonly [string, string, string?]> = [
      ["malformed", "WorkerError"],
      ["truncated", "WorkerError"],
      ["oversized", "WorkerError"],
      ["bad_header_range", "TranscriptError"],
      ["bad_header_pin", "TranscriptError"],
      ["bad_header_segments", "TranscriptError"],
      ["segment_before_header", "WorkerError"],
    ];
    for (const [mode, expectedError, count] of cases) {
      const result = run(["run", inputPath, "--quiet"], {
        FAKE_WORKER_MODE: mode,
        ...(count === undefined ? {} : { FAKE_WORKER_SEGMENTS: count }),
      });
      expect(result.exitCode).toBe(1);
      expect(JSON.parse(result.stderr).error.type).toBe(expectedError);
    }
  });

  test("rejects invalid, unordered, truncated-final, and terminal worker records", () => {
    const cases: ReadonlyArray<readonly [string, string, string?]> = [
      ["bad_segment_range", "TranscriptError"],
      ["out_of_order", "TranscriptError", "2"],
      ["second_header", "WorkerError"],
      ["missing_done", "WorkerError"],
      ["bad_done_count", "WorkerError"],
      ["trailing", "WorkerError"],
    ];
    for (const [mode, expectedError, count] of cases) {
      const result = run(["run", inputPath, "--quiet"], {
        FAKE_WORKER_MODE: mode,
        ...(count === undefined ? {} : { FAKE_WORKER_SEGMENTS: count }),
      });
      expect(result.exitCode).toBe(1);
      expect(JSON.parse(result.stderr).error.type).toBe(expectedError);
    }
  });

  test("maps worker model and media errors to their public error types", () => {
    for (const [mode, expectedType] of [
      ["error_model", "ModelError"],
      ["error_media", "MediaError"],
      ["error_worker", "WorkerError"],
    ] as const) {
      const result = run(["run", inputPath, "--quiet"], { FAKE_WORKER_MODE: mode });
      expect(result.exitCode).toBe(1);
      expect(JSON.parse(result.stderr).error.type).toBe(expectedType);
    }
  });

  test("times out a real CLI whose stdout pipe is held unread", async () => {
    const readyPath = join(directory, "blocked-worker-ready");
    const segmentPath = join(directory, "blocked-worker-segment");
    const script = [
      "import json, os, signal, subprocess, sys",
      "from pathlib import Path",
      "child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)",
      "hung = False",
      "try:",
      " child.wait(timeout=3.5)",
      "except subprocess.TimeoutExpired:",
      " hung = True; os.killpg(child.pid, signal.SIGKILL); child.wait()",
      "stderr = child.stderr.read().decode()",
      "ready = Path(os.environ['FAKE_WORKER_READY'])",
      "alive = False",
      "if ready.exists():",
      " pid = int(ready.read_text())",
      " try: os.kill(pid, 0); alive = True",
      " except ProcessLookupError: pass",
      " if alive:",
      "  try: os.killpg(pid, signal.SIGKILL)",
      "  except ProcessLookupError: pass",
      "child.stdout.close(); child.stderr.close()",
      "print(json.dumps({'exit': child.returncode, 'hung': hung, 'worker_alive': alive, 'segment_emitted': Path(os.environ['FAKE_WORKER_SEGMENT_READY']).exists(), 'stderr': stderr}))",
    ].join("\n");
    const probe = Bun.spawn(
      [
        join(root, ".venv", "bin", "python"),
        "-c",
        script,
        process.execPath,
        "src/cli.ts",
        "run",
        inputPath,
        "--stream",
        "--timeout",
        "1.5",
        "--quiet",
      ],
      {
        cwd: root,
        env: environment({
          FAKE_WORKER_READY: readyPath,
          FAKE_WORKER_SEGMENT_READY: segmentPath,
          FAKE_WORKER_MODE: "gated",
          FAKE_WORKER_GATE: join(directory, "never-release"),
          FAKE_WORKER_TEXT_BYTES: "200000",
        }),
        stdout: "pipe",
        stderr: "pipe",
      },
    );
    const report = await new Response(probe.stdout).json();
    expect(await probe.exited).toBe(0);
    expect(report).toMatchObject({
      exit: 124,
      hung: false,
      worker_alive: false,
      segment_emitted: true,
    });
    expect(JSON.parse(report.stderr).error.type).toBe("TimedOut");
  });

  test("handles a broken stdout pipe and stops the worker", async () => {
    const readyPath = join(directory, "broken-pipe-worker-ready");
    const segmentPath = join(directory, "broken-pipe-worker-segment");
    const child = spawnChild(
      "/bin/bash",
      [
        "-c",
        'set -o pipefail; "$1" src/cli.ts run "$2" --stream --quiet | head -c 1',
        "cli-pipe-test",
        process.execPath,
        inputPath,
      ],
      {
        cwd: root,
        env: environment({
          FAKE_WORKER_READY: readyPath,
          FAKE_WORKER_SEGMENT_READY: segmentPath,
        }),
        stdio: ["ignore", "ignore", "pipe"],
      },
    );
    let stderr = "";
    child.stderr?.setEncoding("utf8");
    child.stderr?.on("data", (chunk: string) => {
      stderr += chunk;
    });
    const exited = exitWithin(child);
    await waitForFile(segmentPath);
    const workerPid = Number(await Bun.file(readyPath).text());
    const exitCode = await exited;
    expect(exitCode).not.toBe(0);
    expect(JSON.parse(stderr).error.type).toBe("WorkerError");
    expect(isProcessAlive(workerPid)).toBe(false);
  });

  test("terminates the worker on Ctrl-C", async () => {
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
    expect(exitCode).toBe(130);
    let workerAlive = true;
    try {
      process.kill(workerPid, 0);
    } catch (cause) {
      if (!(cause instanceof Error) || !("code" in cause) || cause.code !== "ESRCH") throw cause;
      workerAlive = false;
    }
    expect(workerAlive).toBe(false);
  });

  test("Effect fiber interruption reaches the worker", async () => {
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
    } finally {
      for (const [key, value] of previous) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    }
  });
});
