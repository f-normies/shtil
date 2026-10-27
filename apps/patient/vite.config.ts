import { themeColors } from "@compass/ui/tokens";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

const PATIENT_THEME_COLOR_PLACEHOLDER = "__PATIENT_THEME_COLOR__";

const patientThemeColorMetadata = {
  name: "patient-theme-color-metadata",
  transformIndexHtml: {
    order: "pre" as const,
    handler(html: string) {
      return html.replaceAll(PATIENT_THEME_COLOR_PLACEHOLDER, themeColors.patientCool.primary);
    },
  },
};

// PWA пациента по пути /patient/: scope и start_url берутся из base.
// Ответы API сервис-воркер не кэширует: данные пациента не оседают в кэше браузера.
export default defineConfig({
  base: "/patient/",
  plugins: [
    patientThemeColorMetadata,
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "prompt",
      includeAssets: ["apple-touch-icon.png"],
      manifest: {
        name: "Штиль",
        short_name: "Штиль",
        description: "Отметки о самочувствии и связь с лечащим врачом",
        lang: "ru",
        display: "standalone",
        theme_color: themeColors.patientCool.primary,
        background_color: themeColors.patientCool.canvas,
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,woff2}"],
        navigateFallbackDenylist: [/^\/api\//],
      },
    }),
  ],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
      // Второе приложение — через этот же origin, как на стенде: ссылки-приглашения
      // и переходы между кабинетами работают без смены порта.
      "/doctor": { target: "http://localhost:5173", ws: true },
    },
  },
});
