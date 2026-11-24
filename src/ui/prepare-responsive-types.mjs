import loadConfig from "tailwindcss/loadConfig.js";
import { fileURLToPath, URL } from "node:url";

// Tailwind Variants 0.3 generates responsive types when Tailwind loads its config.
// Run before TypeScript so clean installs use the same screens as the CSS build.
loadConfig(fileURLToPath(new URL("./tailwind.config.js", import.meta.url)));
