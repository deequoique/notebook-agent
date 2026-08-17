import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { allApiHostPermissions, apiHostPermission, buildTarget } from "./build-targets.mjs";

const root = resolve(import.meta.dirname, "..");
const target = buildTarget(process.argv[2]);
mkdirSync(resolve(root, "dist"), { recursive: true });
for (const name of ["popup.html", "popup.css"]) {
  copyFileSync(resolve(root, name), resolve(root, "dist", name));
}

const manifest = JSON.parse(readFileSync(resolve(root, "manifest.json"), "utf8"));
manifest.host_permissions = manifest.host_permissions.filter(
  (permission) => !allApiHostPermissions.includes(permission),
);
manifest.host_permissions.push(apiHostPermission(target));
writeFileSync(resolve(root, "dist", "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
