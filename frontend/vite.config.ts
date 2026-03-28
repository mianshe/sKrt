import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/apple-touch-icon.png"],
      manifest: {
        name: "sKrt",
        short_name: "sKrt",
        description: "sKrt 资料解析工具",
        start_url: "/",
        scope: "/",
        display: "standalone",
        theme_color: "#312e81",
        background_color: "#312e81",
        icons: [
          {
            src: "/icons/pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/icons/pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            src: "/icons/pwa-512x512-maskable.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        // 不把 index.html 打进 precache，避免旧 SW 长期返回旧壳导致一直加载旧 hash 的 JS
        globPatterns: ["**/*.{js,css,ico,png,svg,woff2}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
  define: {
    __API_BASE__: JSON.stringify(process.env.VITE_API_BASE || "http://localhost:8000"),
  },
});
