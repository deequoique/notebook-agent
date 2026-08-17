import { describe, expect, it } from "vitest";

import { contentHash } from "./protocol.js";

describe("capture content hash", () => {
  it("matches the backend canonical cue representation", async () => {
    await expect(contentHash([{ start_sec: 1, end_sec: 2.5, text: " hello " }])).resolves.toBe("46ef429a49f8a883f1541705fdf216c206d5c2059e8b4b6962965b8bc9b317b2");
  });
});
