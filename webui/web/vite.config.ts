import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 開発中は Vite(5173)から API(FastAPI、8000)に中継する。ビルドした画面は FastAPI が配る
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
