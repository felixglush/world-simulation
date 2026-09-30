import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  base: "./",
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  plugins: [tailwindcss(), viteSingleFile()],
  server: { host: "127.0.0.1", port: 4173, strictPort: true },
});
