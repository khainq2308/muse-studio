import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
      "src/lib/api/schema.d.ts",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "Use the shared authenticated API client." },
        { name: "localStorage", message: "Admin credentials must remain memory-only." },
        { name: "sessionStorage", message: "Admin credentials must remain memory-only." },
      ],
    },
  },
  {
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
  {
    files: ["src/lib/api/client.ts"],
    rules: {
      // This is the sole module that should issue raw fetch requests.
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Admin credentials must remain memory-only." },
        { name: "sessionStorage", message: "Admin credentials must remain memory-only." },
      ],
    },
  },
  {
    files: ["src/entities/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "@/features/**",
                "../features/**",
                "../../features/**",
                "../../../features/**",
                "../../../../features/**",
              ],
              message:
                "Entities must not depend on features; place shared hooks in entities or lib.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/components/ui/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "@/features/**",
                "@/entities/**",
                "@/lib/api/**",
                "../features/**",
                "../../features/**",
                "../../../features/**",
                "../../../../features/**",
                "../entities/**",
                "../../entities/**",
                "../../../entities/**",
                "../../../../entities/**",
                "../lib/api/**",
                "../../lib/api/**",
                "../../../lib/api/**",
                "../../../../lib/api/**",
              ],
              message:
                "UI primitives must remain domain-agnostic and must not access API transport.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "e2e/**/*.{ts,tsx}"],
    rules: {
      // Tests intentionally inspect storage to prove secrets are not persisted.
      "no-restricted-globals": "off",
    },
  },
  prettier,
);
