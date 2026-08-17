import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    coverage: {
      provider: "v8",
      include: ["src/page-capture.ts"],
      reporter: ["text"],
      thresholds: {
        statements: 80,
        branches: 72,
        functions: 85,
        lines: 90,
      },
    },
  },
});
