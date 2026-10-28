export type AppErrorTag =
  | "InputError"
  | "DependencyError"
  | "MediaError"
  | "SetupError"
  | "ModelError"
  | "WorkerError"
  | "TranscriptError"
  | "OutputExists"
  | "Interrupted"
  | "TimedOut"
  | "InternalError";

export type AppError = {
  readonly _tag: AppErrorTag;
  readonly message: string;
  readonly exitCode: number;
};

export class AppFailure extends Error {
  readonly _tag = "AppFailure";

  constructor(
    readonly error: AppError,
    options?: ErrorOptions,
  ) {
    super(error.message, options);
    this.name = error._tag;
  }
}

export function appError(
  _tag: AppErrorTag,
  message: string,
  exitCode = _tag === "InputError"
    ? 2
    : _tag === "Interrupted"
      ? 130
      : _tag === "TimedOut"
        ? 124
        : 1,
): AppFailure {
  return new AppFailure({ _tag, message, exitCode });
}

export function asAppError(cause: unknown): AppError {
  if (cause instanceof AppFailure) return cause.error;
  if (cause instanceof Error) {
    return {
      _tag: "InternalError",
      message: `unexpected internal error: ${cause.message}`,
      exitCode: 70,
    };
  }
  return {
    _tag: "InternalError",
    message: `unexpected internal error: ${String(cause)}`,
    exitCode: 70,
  };
}
