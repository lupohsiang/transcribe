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
  readonly onStdout?: (chunk: string) => void | Promise<void>;
  readonly captureStdout?: boolean;
};

async function readText(
  stream: ReadableStream<Uint8Array> | number | null | undefined,
  onChunk?: (chunk: string) => void | Promise<void>,
  limit = 128 * 1024 * 1024,
  capture = true,
  fatalUtf8 = false,
): Promise<string> {
  if (stream === null || stream === undefined || typeof stream === "number") return "";
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: fatalUtf8 });
  let result = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      if (capture) result = (result + chunk).slice(-limit);
      await onChunk?.(chunk);
    }
    const finalChunk = decoder.decode();
    if (capture) result = (result + finalChunk).slice(-limit);
    if (finalChunk.length > 0) await onChunk?.(finalChunk);
    return result;
  } catch (cause) {
    await reader.cancel(cause).catch(() => undefined);
    throw cause;
  } finally {
    reader.releaseLock();
  }
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
  const callbackAbort = new AbortController();
  let terminationCause: unknown;
  const terminate = (cause?: unknown): void => {
    if (forceKillTimer !== undefined || terminationCause !== undefined) return;
    terminationCause = cause;
    callbackAbort.abort(cause);
    killChild("SIGTERM");
    forceKillTimer = setTimeout(() => killChild("SIGKILL"), 1500);
  };
  const onAbort = (): void => {
    const signal = options.signal;
    if (signal !== undefined) terminate(interruption(signal));
  };
  options.signal?.addEventListener("abort", onAbort, { once: true });
  if (options.signal?.aborted) terminate(interruption(options.signal));

  const stdoutReader = async (chunk: string): Promise<void> => {
    try {
      if (callbackAbort.signal.aborted) throw callbackAbort.signal.reason;
      if (options.onStdout === undefined) return;
      const callback = Promise.resolve().then(() => options.onStdout?.(chunk));
      let removeAbortListener = (): void => undefined;
      const stopped = new Promise<never>((_resolve, reject) => {
        const onStop = (): void => reject(callbackAbort.signal.reason);
        if (callbackAbort.signal.aborted) onStop();
        else {
          callbackAbort.signal.addEventListener("abort", onStop, { once: true });
          removeAbortListener = () => callbackAbort.signal.removeEventListener("abort", onStop);
        }
      });
      try {
        await Promise.race([callback, stopped]);
      } finally {
        removeAbortListener();
      }
    } catch (cause) {
      terminate(cause);
      throw cause;
    }
  };
  const stdoutPromise = readText(
    child.stdout,
    options.onStdout === undefined ? undefined : stdoutReader,
    128 * 1024 * 1024,
    options.captureStdout !== false,
    options.onStdout !== undefined,
  );
  const stderrPromise = readText(child.stderr, options.onStderr, 256 * 1024);
  const exitedPromise = child.exited;

  try {
    const [stdout, stderr, exitCode] = await Promise.all([
      stdoutPromise,
      stderrPromise,
      exitedPromise,
    ]);
    if (options.signal?.aborted) throw interruption(options.signal);
    return { stdout, stderr, exitCode };
  } catch (cause) {
    terminate(cause);
    await Promise.allSettled([stdoutPromise, stderrPromise, exitedPromise]);
    throw options.signal?.aborted ? interruption(options.signal) : (terminationCause ?? cause);
  } finally {
    if (forceKillTimer !== undefined) clearTimeout(forceKillTimer);
    options.signal?.removeEventListener("abort", onAbort);
  }
}
