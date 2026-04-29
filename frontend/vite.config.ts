import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiBase = (env.VITE_API_BASE || "").trim() || "/api";

  const apiProxy = {
    "/api": {
      target: "http://127.0.0.1:8000",
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/api/, ""),
      secure: false,
      ws: true,
    },
  };

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: "autoUpdate",
        includeAssets: ["icons/apple-touch-icon.png"],
        manifest: {
          id: "/",
          name: "sKrt",
          short_name: "sKrt",
          description: "sKrt document analyzer",
          start_url: "/",
          scope: "/",
          display: "standalone",
          display_override: ["window-controls-overlay", "standalone", "minimal-ui"],
          orientation: "any",
          lang: "zh-CN",
          theme_color: "#312e81",
          background_color: "#312e81",
          categories: ["education", "productivity", "utilities"],
          icons: [
            { src: "/icons/pwa-192x192.png", sizes: "192x192", type: "image/png" },
            { src: "/icons/pwa-512x512.png", sizes: "512x512", type: "image/png" },
            {
              src: "/icons/pwa-512x512-maskable.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "maskable",
            },
          ],
        },
        workbox: {
          globPatterns: ["**/*.{js,css,ico,png,svg,woff2}"],
          navigateFallback: "/index.html",
          navigateFallbackDenylist: [/^\/api\//],
        },
        devOptions: {
          enabled: false,
        },
      }),
    ],
    // Use browser at http://127.0.0.1:5173 so relative /api hits Vite proxy.
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: apiProxy,
    },
    // Reuse /api proxy for local production preview (`vite preview`).
    preview: {
      host: "0.0.0.0",
      port: 4173,
      proxy: apiProxy,
    },
    define: {
      __API_BASE__: JSON.stringify(apiBase),
    },
  };
});
