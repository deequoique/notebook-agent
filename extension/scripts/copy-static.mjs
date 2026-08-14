import { copyFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
mkdirSync(resolve(root, "dist"), { recursive: true });
for (const name of ["manifest.json", "popup.html", "popup.css"]) {
  copyFileSync(resolve(root, name), resolve(root, "dist", name));
}
