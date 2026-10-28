import { copyFile, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dir, "..");
const output = join(root, "dist", "release");
const bundle = join(output, "transcribe-darwin-arm64");
await rm(output, { recursive: true, force: true });
await mkdir(join(bundle, "python"), { recursive: true });

async function run(args: string[]): Promise<void> {
  const child = Bun.spawn(args, {
    cwd: root,
    env: { ...process.env, LC_ALL: "C" },
    stdout: "inherit",
    stderr: "inherit",
  });
  if ((await child.exited) !== 0) throw new Error(`Failed: ${args[0]}`);
}

await run([
  process.execPath,
  "build",
  "src/cli.ts",
  "--compile",
  "--target=bun-darwin-arm64",
  "--minify",
  "--define",
  "TRANSCRIBE_COMPILED=true",
  "--outfile",
  join(bundle, "transcribe"),
]);
const assets = ["python/worker.py", "model-pins.json", "pyproject.toml", "uv.lock"];
for (const asset of assets) await copyFile(join(root, asset), join(bundle, asset));
const archive = "transcribe-darwin-arm64.tar.gz";
await run(["tar", "-czf", join(output, archive), "-C", bundle, "transcribe", ...assets]);
const hash = new Bun.CryptoHasher("sha256")
  .update(await Bun.file(join(output, archive)).arrayBuffer())
  .digest("hex");
await Bun.write(join(output, "SHA256SUMS"), `${hash}  ${archive}\n`);
await copyFile(join(root, "install.sh"), join(output, "install.sh"));
console.log(`Release files: ${output}`);
