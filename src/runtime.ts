import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";

declare const TRANSCRIBE_COMPILED: boolean;

/** Native releases keep the Python worker beside the executable, including through symlinks. */
export function getRuntimeDirectory(): string {
  return typeof TRANSCRIBE_COMPILED !== "undefined" && TRANSCRIBE_COMPILED
    ? dirname(realpathSync(process.execPath))
    : resolve(import.meta.dir, "..");
}
