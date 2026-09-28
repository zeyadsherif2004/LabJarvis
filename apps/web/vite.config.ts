import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { configDefaults, defineConfig } from "vitest/config";

/**
 * Serve the hands-free detector's runtime files from public/vad as plain files.
 *
 * onnxruntime-web loads its WASM glue with a runtime `import(url)`. In the dev
 * server Vite rewrites that request to `/vad/ort-wasm-simd-threaded.mjs?import`
 * and then refuses to serve a public/ file as an import (HTTP 500), so the
 * detector failed with "no available backend found" and hands-free listening
 * never started. Dropping the query before Vite's own middleware lets the
 * public-file handler serve it. Production builds never add the query.
 */
function servePublicVadAssets(): Plugin {
  return {
    name: "labjarvis-serve-public-vad-assets",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((request, _response, next) => {
        if (request.url?.startsWith("/vad/") && request.url.includes("?")) {
          request.url = request.url.slice(0, request.url.indexOf("?"));
        }
        next();
      });
    },
  };
}

const apiProxy = process.env.LABJARVIS_API_PROXY ?? "http://127.0.0.1:8000";
const webPort = Number(process.env.LABJARVIS_WEB_PORT ?? "5173");

export default defineConfig({
  plugins: [react(), servePublicVadAssets()],
  server: {
    port: webPort,
    strictPort: true,
    proxy: {
      "/api": apiProxy,
      "/health": apiProxy,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
