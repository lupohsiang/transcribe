import { randomUUID } from "node:crypto";
import { link, open, rename, unlink } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { appError } from "./errors";

export async function writeOutput(
  targetPath: string,
  contents: string,
  force: boolean,
): Promise<void> {
  const destination = resolve(targetPath);
  const temporary = `${dirname(destination)}/.${basename(destination)}.${process.pid}.${randomUUID()}.tmp`;
  try {
    const handle = await open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(contents, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }

    if (force) {
      await rename(temporary, destination);
      return;
    }
    try {
      await link(temporary, destination);
    } catch (cause) {
      if (cause instanceof Error && "code" in cause && cause.code === "EEXIST") {
        throw appError(
          "OutputExists",
          `output already exists: ${destination}; use --force to replace it`,
        );
      }
      throw cause;
    }
  } finally {
    await unlink(temporary).catch(() => undefined);
  }
}
