import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import zlib from "node:zlib";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// A Brotli (quality 11) and a gzip (level 9) copy beside every emitted file
// worth compressing, made once per build instead of per request: the backend
// sends the copy a request accepts (gamma/app.py), and so does a reverse proxy
// serving dist itself (docs/dev/debugging.md, "Serving the build").
const COMPRESSIBLE = /\.(js|mjs|css|html|svg)$/;
const MIN_BYTES = 1024;
const brotli = promisify(zlib.brotliCompress);
const gzip = promisify(zlib.gzip);

function precompress() {
  let outDir;
  return {
    name: "gamma-precompress",
    apply: "build",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    async closeBundle() {
      const entries = await fs.readdir(outDir, { recursive: true, withFileTypes: true });
      const files = entries.filter((e) => e.isFile() && COMPRESSIBLE.test(e.name))
        .map((e) => path.join(e.parentPath, e.name));
      await Promise.all(files.map(async (file) => {
        const data = await fs.readFile(file);
        if (data.length < MIN_BYTES) return;
        const copies = await Promise.all([
          brotli(data, { params: {
            [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
            [zlib.constants.BROTLI_PARAM_SIZE_HINT]: data.length,
          } }).then((body) => [".br", body]),
          gzip(data, { level: 9 }).then((body) => [".gz", body]),
        ]);
        await Promise.all(copies.filter(([, body]) => body.length < data.length)
          .map(([ext, body]) => fs.writeFile(file + ext, body)));
      }));
    },
  };
}

export default defineConfig({
  plugins: [react(), precompress()],
  server: {
    proxy: {
      // ws: the page's live socket (/api/ws/page/…) rides the same proxy
      "/api": { target: "http://127.0.0.1:9001", ws: true },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    allowedHosts: ["annotation.amogadgetlab.com"]
  }
});
