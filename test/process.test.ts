import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProcess } from "../src/process";

let directory = "";
beforeEach(async () => {
  directory = await mkdtemp(join(tmpdir(), "transcribe-process-test-"));
});
afterEach(async () => {
  await rm(directory, { recursive: true, force: true });
});

function deferred() {
  let resolve = (): void => undefined;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("streams split UTF-8 with backpressure and without retaining stdout", async () => {
  const gate = join(directory, "continue");
  const chunks: string[] = [];
  const result = await runProcess(
    process.execPath,
    [
      "-e",
      "import { existsSync } from 'node:fs'; process.stdout.write(Buffer.from([97,228])); while (!existsSync(process.env.STREAM_TEST_GATE)) await Bun.sleep(1); process.stdout.write(Buffer.from([189,160,10]));",
    ],
    {
      env: { ...process.env, STREAM_TEST_GATE: gate },
      captureStdout: false,
      onStdout: async (chunk) => {
        chunks.push(chunk);
        if (chunk === "a") await writeFile(gate, "continue");
      },
    },
  );
  expect(chunks).toEqual(["a", "你\n"]);
  expect(result).toEqual({ stdout: "", stderr: "", exitCode: 0 });
});

test("cancellation reaps a child while its stdout observer never resolves", async () => {
  const controller = new AbortController();
  const entered = deferred();
  let childPid = 0;
  const running = runProcess(
    process.execPath,
    ["-e", "process.stdout.write(String(process.pid)); setInterval(() => {}, 1000);"],
    {
      signal: controller.signal,
      captureStdout: false,
      onStdout: async (chunk) => {
        childPid = Number(chunk);
        entered.resolve();
        await new Promise<void>(() => undefined);
      },
    },
  );
  try {
    await entered.promise;
    controller.abort();
    await expect(running).rejects.toMatchObject({ error: { _tag: "Interrupted", exitCode: 130 } });
    expect(() => process.kill(childPid, 0)).toThrow();
  } finally {
    controller.abort();
    await running.catch(() => undefined);
  }
});

test("stderr failure releases a pending stdout observer and reaps the child", async () => {
  const controller = new AbortController();
  const gate = join(directory, "stderr");
  const failure = new Error("diagnostic sink rejected");
  let childPid = 0;
  const running = runProcess(
    process.execPath,
    [
      "-e",
      "import { existsSync } from 'node:fs'; process.stdout.write(String(process.pid)); while (!existsSync(process.env.STREAM_TEST_GATE)) await Bun.sleep(1); process.stderr.write('diagnostic'); setInterval(() => {}, 1000);",
    ],
    {
      env: { ...process.env, STREAM_TEST_GATE: gate },
      signal: controller.signal,
      captureStdout: false,
      onStdout: async (chunk) => {
        childPid = Number(chunk);
        await writeFile(gate, "ready");
        await new Promise<void>(() => undefined);
      },
      onStderr: () => {
        throw failure;
      },
    },
  );
  try {
    await expect(running).rejects.toBe(failure);
    expect(() => process.kill(childPid, 0)).toThrow();
  } finally {
    controller.abort();
    await running.catch(() => undefined);
  }
});
