import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const assets = "../../plugins/astrbot_plugin_qing_chat/features/raffle/assets";
const licenseFile = "raffle-THIRD-PARTY.txt";

export default defineConfig({
  plugins: [{
    name: "boardui-license-notice",
    apply: "build",
    writeBundle() {
      const file = here(assets + "/" + licenseFile);
      const bundledLicenses = readFileSync(file, "utf8");
      writeFileSync(file, readFileSync(here("./boardui/LICENSE"), "utf8") + "\n" + bundledLicenses);
    },
  }],
  resolve: { alias: { "@": here("./boardui") } },
  build: {
    outDir: here(assets),
    license: { fileName: licenseFile },
    emptyOutDir: false,
    assetsDir: "",
    rollupOptions: {
      input: here("./app.tsx"),
      output: {
        entryFileNames: "raffle.js",
        assetFileNames: "raffle.[ext]",
        banner: "/*! See raffle-THIRD-PARTY.txt for licensing information. */",
      },
    },
  },
});
