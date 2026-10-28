#!/usr/bin/env bun
import { realpath, stat } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { Command, CommanderError, Option } from "commander";
import { Effect } from "effect";
import packageJson from "../package.json";
import { inspectEnvironment, inspectModels, setupModels } from "./environment";
import { type AppError, AppFailure, appError } from "./errors";
import { formatTranscript, type TranscriptFormat } from "./format";
import { writeOutput } from "./output";
import { transcribeEffect } from "./transcriber";
import { transcriptJsonSchema } from "./transcript";

const program = new Command();
const VERSION = packageJson.version;

function withSignal<T>(run: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  const onInterrupt = (): void => controller.abort(new DOMException("interrupted", "AbortError"));
  const onTerminate = (): void => controller.abort(new DOMException("terminated", "AbortError"));
  process.once("SIGINT", onInterrupt);
  process.once("SIGTERM", onTerminate);
  return run(controller.signal).finally(() => {
    process.off("SIGINT", onInterrupt);
    process.off("SIGTERM", onTerminate);
  });
}

function reportError(error: AppError): void {
  process.stderr.write(
    `${JSON.stringify({ error: { type: error._tag, message: error.message, exitCode: error.exitCode } })}\n`,
  );
  process.exitCode = error.exitCode;
}

function parseFailure(cause: unknown): AppError {
  if (cause instanceof AppFailure) return cause.error;
  if (cause instanceof CommanderError) {
    return { _tag: "InputError", message: cause.message, exitCode: cause.exitCode || 2 };
  }
  if (cause instanceof Error) {
    return { _tag: "WorkerError", message: cause.message, exitCode: 1 };
  }
  return { _tag: "WorkerError", message: String(cause), exitCode: 1 };
}

function parseFiniteNumber(value: string, name: string): number {
  if (value.trim().length === 0) throw appError("InputError", `--${name} requires a number`);
  const number = Number(value);
  if (!Number.isFinite(number)) throw appError("InputError", `--${name} requires a finite number`);
  return number;
}

function parseStart(value: string): number {
  const number = parseFiniteNumber(value, "start");
  if (number < 0) throw appError("InputError", "--start must be greater than or equal to 0");
  return number;
}

function parsePositiveOption(value: string, name: "duration" | "timeout"): number {
  const number = parseFiniteNumber(value, name);
  if (number <= 0) throw appError("InputError", `--${name} must be greater than 0`);
  return number;
}

async function validateOutputPath(
  inputPath: string,
  outputPath: string,
  force: boolean,
): Promise<string> {
  const source = resolve(inputPath);
  const target = resolve(outputPath);
  let sourceInfo: Awaited<ReturnType<typeof stat>>;
  try {
    sourceInfo = await stat(source);
  } catch {
    throw appError("InputError", `audio file not found: ${inputPath}`);
  }
  const sourceRealPath = await realpath(source);
  try {
    const targetInfo = await stat(target);
    if (sourceInfo.dev === targetInfo.dev && sourceInfo.ino === targetInfo.ino) {
      throw appError("InputError", "output path must not replace the input audio");
    }
    if (!force)
      throw appError("OutputExists", `output already exists: ${target}; use --force to replace it`);
  } catch (cause) {
    if (cause instanceof AppFailure) throw cause;
    if (cause instanceof Error && "code" in cause && cause.code === "ENOENT") {
      const parent = await realpath(dirname(target)).catch(() => {
        throw appError("InputError", `output directory does not exist: ${dirname(target)}`);
      });
      if (join(parent, basename(target)) === sourceRealPath) {
        throw appError("InputError", "output path must not replace the input audio");
      }
    } else {
      throw cause;
    }
  }
  return target;
}

program
  .name("transcribe")
  .description("Transcribe local audio with speaker diarization")
  .version(VERSION)
  .exitOverride()
  .configureOutput({
    writeOut: (text) => process.stdout.write(text),
    writeErr: (text) => process.stderr.write(text),
    outputError: () => undefined,
  });

program
  .command("run")
  .description("Transcribe an audio file and label overlapping speakers")
  .argument("<file>", "audio file to transcribe")
  .addOption(
    new Option("-f, --format <format>", "output format")
      .choices(["json", "text", "srt"])
      .default("json"),
  )
  .option("--json", "shorthand for --format json")
  .option("-o, --output <path>", "write to a file instead of stdout")
  .option("--force", "replace an existing output file")
  .option("--start <seconds>", "start time in the source file", (value) => parseStart(value), 0)
  .option("--duration <seconds>", "selected audio duration", (value) =>
    parsePositiveOption(value, "duration"),
  )
  .option("--language <name>", "ASR language hint, for example Chinese or English")
  .addOption(
    new Option("--chinese-script <script>", "Chinese output script")
      .choices(["zh-TW", "original"])
      .default("zh-TW"),
  )
  .option(
    "--timeout <seconds>",
    "stop the run after this many seconds",
    (value) => parsePositiveOption(value, "timeout"),
    21600,
  )
  .option("-q, --quiet", "suppress progress messages")
  .addHelpText(
    "after",
    "\nExamples:\n  transcribe run recording.m4a\n  transcribe run recording.m4a --format srt --output recording.srt\n  transcribe run recording.m4a --start 60 --duration 120 --json\n",
  )
  .action(
    async (
      file: string,
      options: {
        readonly format: string;
        readonly json?: boolean;
        readonly output?: string;
        readonly force?: boolean;
        readonly start: number;
        readonly duration?: number;
        readonly language?: string;
        readonly chineseScript: string;
        readonly timeout: number;
        readonly quiet?: boolean;
      },
    ) => {
      const format = options.json === true ? "json" : options.format;
      if (format !== "json" && format !== "text" && format !== "srt") {
        throw appError("InputError", "--format must be one of json, text, or srt");
      }
      const chineseScript = options.chineseScript;
      if (chineseScript !== "zh-TW" && chineseScript !== "original") {
        throw appError("InputError", "--chinese-script must be zh-TW or original");
      }
      const startSeconds = options.start;
      const durationSeconds = options.duration;
      const timeoutSeconds = options.timeout;
      const outputPath = options.output === "-" ? undefined : options.output;
      const force = options.force === true;
      const quiet = options.quiet === true;
      const target =
        outputPath === undefined ? undefined : await validateOutputPath(file, outputPath, force);
      const transcript = await withSignal((signal) => {
        const result = Effect.match(
          transcribeEffect({
            inputPath: file,
            startSeconds,
            durationSeconds,
            language: options.language,
            timeoutMs: timeoutSeconds * 1000,
            signal,
            quiet,
          }),
          {
            onFailure: (error) => ({ ok: false as const, error }),
            onSuccess: (value) => ({ ok: true as const, value }),
          },
        );
        return Effect.runPromise(result).then((outcome) => {
          if (!outcome.ok) throw new AppFailure(outcome.error);
          return outcome.value;
        });
      });
      const output = formatTranscript(transcript, format as TranscriptFormat, chineseScript);
      if (target === undefined) {
        process.stdout.write(output);
      } else {
        await writeOutput(target, output, force);
      }
    },
  );

program
  .command("setup")
  .description("Install offline inference dependencies and download pinned models")
  .option("-q, --quiet", "suppress progress messages")
  .action(async (options: { readonly quiet?: boolean }) =>
    withSignal((signal) => setupModels(signal, options.quiet === true)),
  );

program
  .command("doctor")
  .description("Check local runtime dependencies and installed model files")
  .option("--json", "print checks as JSON")
  .action(async (options: { readonly json?: boolean }) => {
    const diagnostics = await inspectEnvironment();
    if (options.json) {
      process.stdout.write(`${JSON.stringify({ checks: diagnostics }, null, 2)}\n`);
    } else {
      for (const diagnostic of diagnostics) {
        process.stdout.write(
          `${diagnostic.ok ? "ok" : "missing"}  ${diagnostic.name}: ${diagnostic.detail}\n`,
        );
      }
    }
    if (diagnostics.some((diagnostic) => !diagnostic.ok)) process.exitCode = 1;
  });

program
  .command("models")
  .description("Show pinned model revisions and local snapshot status")
  .option("--json", "print model information as JSON")
  .action(async (options: { readonly json?: boolean }) => {
    const models = await inspectModels();
    if (options.json) {
      process.stdout.write(`${JSON.stringify({ models }, null, 2)}\n`);
    } else {
      for (const model of models) {
        process.stdout.write(`${model.ok ? "ready" : "missing"}  ${model.name}: ${model.detail}\n`);
      }
    }
    if (models.some((model) => !model.ok)) process.exitCode = 1;
  });

program
  .command("schema")
  .description("Print the JSON Schema for transcript version 1")
  .action(() => {
    process.stdout.write(`${JSON.stringify(transcriptJsonSchema, null, 2)}\n`);
  });

program.addHelpText(
  "after",
  "\nData is processed locally. Run `transcribe setup` once before the first transcription.\n",
);

try {
  if (process.argv.length <= 2) {
    program.outputHelp();
  } else {
    await program.parseAsync(process.argv);
  }
} catch (cause) {
  if (cause instanceof CommanderError && cause.exitCode === 0) {
    process.exitCode = 0;
  } else {
    reportError(parseFailure(cause));
  }
}
