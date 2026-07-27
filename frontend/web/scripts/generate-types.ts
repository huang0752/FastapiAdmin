import { stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));
const declarationFiles = ["src/types/import/auto-imports.d.ts", "src/types/import/components.d.ts"];

process.chdir(projectRoot);

// Run the complete Vite plugin lifecycle so declarations contain imports that
// are discovered while transforming application modules. `write: false` keeps
// this command limited to type generation and avoids producing a dist bundle.
await build({
  root: projectRoot,
  logLevel: "warn",
  build: {
    emptyOutDir: false,
    minify: false,
    write: false,
  },
});

for (const declarationFile of declarationFiles) {
  const declaration = await stat(declarationFile);
  if (!declaration.isFile() || declaration.size === 0) {
    throw new Error(`Vite did not generate ${declarationFile}`);
  }
}
