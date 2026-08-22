import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webRoot, "..");
const candidates = process.platform === "win32"
  ? [join(repoRoot, ".venv", "Scripts", "python.exe")]
  : [join(repoRoot, ".venv", "bin", "python")];
const python = candidates.find(existsSync);

if (!python) {
  process.stderr.write("Project Python is missing. Create .venv and install -e '.[dev]'.\n");
  process.exit(1);
}

const mode = process.argv[2];
if (mode !== "generate" && mode !== "check") {
  process.stderr.write("Usage: node scripts/openapi.mjs <generate|check>\n");
  process.exit(2);
}

const args = [join(repoRoot, "scripts", "export_web_openapi.py")];
if (mode === "check") args.push("--check");
const result = spawnSync(python, args, {
  cwd: repoRoot,
  env: { ...process.env, PYTHONPATH: repoRoot },
  stdio: "inherit",
});

if (result.error) {
  process.stderr.write(`${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status ?? 1);
