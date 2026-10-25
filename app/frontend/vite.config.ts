import { cpSync, existsSync, rmSync, copyFileSync, createReadStream } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const DEV_PORT = 5190;
/** Demo token for /agent/run and /sim/*: injected by the dev proxy from KCF_DEMO_TOKEN so it never lands in the bundle. */
const DEMO_TOKEN_HEADER = process.env.KCF_DEMO_TOKEN_HEADER ?? "X-Demo-Token";
const DEMO_TOKEN = process.env.KCF_DEMO_TOKEN;
const BACKEND = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

/** Public 시연판 (npm run build:pages): GitHub Pages subpath, recorded fixtures, no .env files (envDir points at a dir holding only VITE_STATIC=1). */
const STATIC_BUILD = process.env.VITE_STATIC === "1";
const HERE = fileURLToPath(new URL(".", import.meta.url));
const PAGES_BASE = process.env.PAGES_BASE ?? "/elder-safety-smartcity-agent/";
const FIXTURE_SRC = resolve(HERE, "static-fixtures");
/** public/ assets the static build never loads (the /app map shards). */
const PAGES_PRUNE: readonly string[] = ["ops"];

function staticPages(): Plugin {
  let outDir = "";
  return {
    name: "kcf-static-pages",
    configResolved(c) { outDir = resolve(c.root, c.build.outDir); },
    transformIndexHtml() {
      // Deep link /<repo>/terminal served as index.html (preview servers, some hosts): same redirect as 404.html.
      const js = `(function(){var b=${JSON.stringify(PAGES_BASE)};if(location.pathname.indexOf(b)!==0)return;var r=location.pathname.slice(b.length).replace(/\\/+$/,"");if(r&&r!=="index.html"&&!location.hash)location.replace(b+"#/"+r+location.search);})();`;
      return [{ tag: "script", children: js, injectTo: "head-prepend" }];
    },
    configureServer(server) {
      server.middlewares.use(`${PAGES_BASE}fixtures`, (req, res, next) => {
        const f = resolve(FIXTURE_SRC, decodeURIComponent((req.url ?? "/").split("?")[0]!.slice(1)));
        if (!f.startsWith(FIXTURE_SRC) || !existsSync(f)) { next(); return; }
        createReadStream(f).pipe(res);
      });
    },
    closeBundle() {
      if (!existsSync(FIXTURE_SRC)) throw new Error("static-fixtures/ missing: run scripts/record_fixtures.mjs first");
      cpSync(FIXTURE_SRC, resolve(outDir, "fixtures"), { recursive: true });
      for (const d of PAGES_PRUNE) rmSync(resolve(outDir, d), { recursive: true, force: true });
      copyFileSync(resolve(HERE, "scripts/pages-404.html"), resolve(outDir, "404.html"));
    },
  };
}

export default defineConfig({
  ...(STATIC_BUILD ? { base: PAGES_BASE, envDir: resolve(HERE, "env-pages") } : {}),
  plugins: [react(), ...(STATIC_BUILD ? [staticPages()] : [])],
  build: { chunkSizeWarningLimit: 700 },
  server: {
    // LAN: the phone/tablet terminal opens http://<laptop-ip>:5190/terminal; the browser only ever talks to this origin (/api proxy).
    host: true,
    port: DEV_PORT,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, ""),
        ...(DEMO_TOKEN === undefined || DEMO_TOKEN === "" ? {} : { headers: { [DEMO_TOKEN_HEADER]: DEMO_TOKEN } }),
      },
    },
  },
  preview: { host: true, port: DEV_PORT, strictPort: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
