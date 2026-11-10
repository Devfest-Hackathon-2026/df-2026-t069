// @ts-nocheck
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";
import { copyFileSync, existsSync, mkdirSync, cpSync, readFileSync, writeFileSync } from "fs";

function copyStaticAssets(mode: string) {
  const isProduction = mode === "production";
  const backendUrl = isProduction
    ? "https://" + "detector-backend-vaggox657a-an.a.run.app"
    : "http://" + "localhost:8080";
  const frontendUrl = isProduction
    ? "https://" + "waiwaiwaiwai.web.app"
    : "http://" + "localhost:3000";

  return {
    name: "copy-static-assets",
    closeBundle() {
      const distDir = resolve(__dirname, "dist");
      const srcDir = resolve(__dirname, "src");

      if (!existsSync(distDir)) mkdirSync(distDir, { recursive: true });

      // manifest.json を dist 直下にコピー
      copyFileSync(resolve(__dirname, "manifest.json"), resolve(distDir, "manifest.json"));

      // service worker, offscreen, permission などの静的ファイルを dist/src にコピー
      const distSrc = resolve(distDir, "src");
      if (!existsSync(distSrc)) mkdirSync(distSrc, { recursive: true });

      const files = [
        "service-worker.js",
        "analysis-api.js",
        "offscreen.html",
        "offscreen.js",
        "permission.html",
        "permission.js",
      ];
      for (const f of files) {
        const p = resolve(srcDir, f);
        if (existsSync(p)) {
          if (f === "service-worker.js" || f === "analysis-api.js") {
            let content = readFileSync(p, "utf8");
            content = content
              .replaceAll("https://" + "detector-backend-vaggox657a-an.a.run.app", backendUrl)
              .replaceAll("https://" + "waiwaiwaiwai.web.app", frontendUrl);
            writeFileSync(resolve(distSrc, f), content);
          } else {
            copyFileSync(p, resolve(distSrc, f));
          }
        }
      }

      // manifest で要求される content.css を dist/src にコピー
      const contentCss = resolve(srcDir, "content/content.css");
      if (existsSync(contentCss)) {
        copyFileSync(contentCss, resolve(distSrc, "content.css"));
      }

      // アイコン画像（icons/）を dist/icons にコピー
      const iconsDir = resolve(__dirname, "icons");
      const distIcons = resolve(distDir, "icons");
      if (existsSync(iconsDir)) {
        if (!existsSync(distIcons)) mkdirSync(distIcons, { recursive: true });
        for (const icon of ["icon16.png", "icon24.png", "icon32.png", "icon48.png", "icon128.png"]) {
          const iconPath = resolve(iconsDir, icon);
          if (existsSync(iconPath)) {
            copyFileSync(iconPath, resolve(distIcons, icon));
          }
        }
      }

      // チュートリアル等の画像（src/images/）を dist/src/images にコピー
      const imagesDir = resolve(srcDir, "images");
      const distImages = resolve(distSrc, "images");
      if (existsSync(imagesDir)) {
        if (!existsSync(distImages)) mkdirSync(distImages, { recursive: true });
        cpSync(imagesDir, distImages, { recursive: true });
      }
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), copyStaticAssets(mode)],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        popup: resolve(__dirname, "src/popup/index.html"),
        content: resolve(__dirname, "src/content/index.ts"),
      },
      output: {
        entryFileNames: (chunkInfo) => {
          if (chunkInfo.name === "content") {
            return "src/content.js";
          }
          return "assets/[name]-[hash].js";
        },
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith(".css")) {
            if (assetInfo.name.includes("content")) {
              return "src/content.css";
            }
          }
          return "assets/[name]-[hash].[ext]";
        },
      },
    },
  },
}));
