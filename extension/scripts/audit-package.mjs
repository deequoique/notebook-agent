import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const manifest = JSON.parse(readFileSync(resolve(dist, "manifest.json"), "utf8"));
const expectedPermissions = ["activeTab", "scripting", "storage"];
const expectedHosts = [
  "https://www.youtube.com/*",
  "https://youtu.be/*",
  "https://ntulearn.ntu.edu.sg/*",
  "https://ntulearnvideo.ntu.edu.sg/*",
  "https://notebookai.deequoique.tech/*",
];

if (JSON.stringify(manifest.permissions) !== JSON.stringify(expectedPermissions)) throw new Error("unexpected extension permissions");
if (JSON.stringify(manifest.host_permissions) !== JSON.stringify(expectedHosts)) throw new Error("unexpected extension hosts");
if (manifest.content_security_policy || manifest.externally_connectable) throw new Error("unexpected extension execution boundary");

const files = readdirSync(dist);
if (files.some((name) => name.endsWith(".map") || name.includes("test"))) throw new Error("test or source-map artifact packaged");
for (const name of files.filter((value) => value.endsWith(".js"))) {
  const body = readFileSync(resolve(dist, name), "utf8");
  if (/sourceMappingURL|<script\b|eval\s*\(|new\s+Function\s*\(/.test(body)) throw new Error(`remote/dynamic code marker in ${name}`);
}
