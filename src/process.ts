import { type AppFailure, appError } from "./errors";

export type ProcessResult = {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number;
};

export type ProcessOptions = {
  readonly stdin?: string;
  readonly signal?: AbortSignal;
  readonly env?: Record<string, string | undefined>;
  readonly onStderr?: (chunk: string) => void;
};

async function readText(
  stream: ReadableStream<Uint8Array> | number | null | undefined,
  onChunk?: (chunk: string) => void,
  limit = 128 * 1024 * 1024,
): Promise<string> {
  if (stream === null || stream === undefined || typeof stream === "number") return "";
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let result = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    result = (result + chunk).slice(-limit);
    onChunk?.(chunk);
  }
  result = (result + decoder.decode()).slice(-limit);
  return result;
}

function interruption(signal: AbortSignal): AppFailure {
  const reason = signal.reason;
  if (reason instanceof Error && reason.name === "TimeoutError") {
    return appError("TimedOut", "transcription exceeded the configured timeout");
  }
  return appError("Interrupted", "transcription interrupted");
}

export async function runProcess(
  command: string,
  args: ReadonlyArray<string>,
  options: ProcessOptions = {},
): Promise<ProcessResult> {
  if (options.signal?.aborted) throw interruption(options.signal);

  let child: Bun.Subprocess;
  try {
    child = Bun.spawn([command, ...args], {
      stdin: options.stdin === undefined ? "ignore" : "pipe",
      stdout: "pipe",
      stderr: "pipe",
      ...(options.env === undefined ? {} : { env: options.env }),
      detached: process.platform !== "win32",
    });
  } catch (cause) {
    throw appError("DependencyError", `could not start ${command}: ${String(cause)}`, 69);
  }

  const stdin = child.stdin;
  if (
    options.stdin !== undefined &&
    stdin !== null &&
    stdin !== undefined &&
    typeof stdin !== "number"
  ) {
    stdin.write(options.stdin);
    stdin.end();
  }

  let forceKillTimer: ReturnType<typeof setTimeout> | undefined;
  const killChild = (signal: "SIGTERM" | "SIGKILL"): void => {
    if (process.platform === "win32") {
      child.kill(signal);
      return;
    }
    try {
      process.kill(-child.pid, signal);
    } catch {
      try {
        child.kill(signal);
      } catch {
        // The process may have exited between a signal and its delivery.
      }
    }
  };
  const terminate = (): void => {
    killChild("SIGTERM");
    forceKillTimer = setTimeout(() => killChild("SIGKILL"), 1500);
  };
  const onAbort = (): void => terminate();
  options.signal?.addEventListener("abort", onAbort, { once: true });
  if (options.signal?.aborted) terminate();

  try {
    const [stdout, stderr, exitCode] = await Promise.all([
      readText(child.stdout),
      readText(child.stderr, options.onStderr, 256 * 1024),
      child.exited,
    ]);
    if (options.signal?.aborted) throw interruption(options.signal);
    return { stdout, stderr, exitCode };
  } finally {
    if (forceKillTimer !== undefined) clearTimeout(forceKillTimer);
    options.signal?.removeEventListener("abort", onAbort);
  }
}
