import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Кабинет врача живёт по пути /doctor/ (на стенде Caddy отдаёт его там же).
// В разработке /api и WebSocket проксируются на локальный API без смены Host:
// так совпадают Origin и Host, и проверка источника WebSocket проходит.
export default defineConfig({
  base: "/doctor/",
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
      // Второе приложение — через этот же origin, как на стенде: ссылки-приглашения
      // и переходы между кабинетами работают без смены порта.
      "/patient": { target: "http://localhost:5174", ws: true },
    },
  },
});
