import eslint from "@eslint/js";
import prettierRecommended from "eslint-plugin-prettier/recommended";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

/**
 * @type {import("eslint").Linter.Config}
 */
const config = {
    plugins: {
        "react-refresh": reactRefresh,
    },
    rules: {
        quotes: ["error", "double", { avoidEscape: true }],
        semi: [2, "always"],
        indent: ["off"],
        "@typescript-eslint/no-unused-vars": "off",
        "@typescript-eslint/keyword-spacing": "off",
        "@/max-len": ["error", { code: 150 }],
        "@typescript-eslint/no-empty-function": "off",
        "@typescript-eslint/no-empty-object-type": "off",
    },
};

export default [eslint.configs.recommended, ...tseslint.configs.recommended, prettierRecommended, config];
