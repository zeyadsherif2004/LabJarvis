// Copy the Silero VAD model, its audio worklet and the onnxruntime-web WASM
// files into public/vad so hands-free listening works offline, with no CDN.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const target = join(process.cwd(), "public", "vad");
mkdirSync(target, { recursive: true });
const vadDist = dirname(require.resolve("@ricky0123/vad-web/package.json")) + "/dist";
for (const file of ["silero_vad_v5.onnx", "vad.worklet.bundle.min.js"]) {
  copyFileSync(join(vadDist, file), join(target, file));
}
// onnxruntime-web does not export package.json; it sits beside vad-web in pnpm's store.
const ortDist = join(vadDist, "..", "..", "..", "onnxruntime-web", "dist");
if (existsSync(ortDist)) {
  for (const file of readdirSync(ortDist)) {
    if (/^ort-wasm-simd-threaded\.(wasm|mjs)$/.test(file)) copyFileSync(join(ortDist, file), join(target, file));
  }
}
console.log(`copied VAD assets to ${target}`);
