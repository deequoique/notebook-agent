import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ["src/**/*.ts"], languageOptions: { globals: { ...globals.browser, ...globals.webextensions } } },
);
