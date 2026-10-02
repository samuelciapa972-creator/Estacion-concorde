import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "dev-dist", "node_modules", "src/api/esquema.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      // Dinero y galones nunca como número de punto flotante: ver src/lib/formato.ts
      "no-restricted-globals": ["error", { name: "parseFloat", message: "Dinero y galones van como texto decimal" }],
      "no-restricted-properties": [
        "error",
        { object: "Number", property: "parseFloat", message: "Dinero y galones van como texto decimal" },
      ],
    },
  },
);
