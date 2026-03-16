import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        t: {
          bg: "#0a0e17",
          panel: "#0f1419",
          "panel-alt": "#141a22",
          "panel-hover": "#171f2a",
          border: "#1c2433",
          "border-light": "#2a3545",
        },
        accent: {
          DEFAULT: "#3b82f6",
          dim: "rgba(59, 130, 246, 0.2)",
        },
        profit: {
          DEFAULT: "#00d26a",
          dim: "rgba(0, 210, 106, 0.18)",
        },
        loss: {
          DEFAULT: "#ff4757",
          dim: "rgba(255, 71, 87, 0.18)",
        },
        warn: {
          DEFAULT: "#f0b429",
          dim: "rgba(240, 180, 41, 0.18)",
        },
        txt: {
          primary: "#e8edf5",
          secondary: "#a3aec2",
          muted: "#7a879b",
        },
      },
      fontFamily: {
        mono: [
          "JetBrains Mono",
          "SF Mono",
          "Fira Code",
          "ui-monospace",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
