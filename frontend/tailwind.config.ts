import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        page: "#f0f0f0",
        surface: "#ffffff",
        sidebar: "#fafafa",
        ink: {
          DEFAULT: "#111111",
          dim: "#333333",
          muted: "#555555",
          subtle: "#888888",
          faint: "#aaaaaa",
          ghost: "#bbbbbb",
          line: "#cccccc",
        },
        rule: {
          DEFAULT: "#e0e0e0",
          soft: "#e8e8e8",
          faint: "#eeeeee",
          ghost: "#f5f5f5",
        },
        terminal: {
          bg: "#111111",
          fg: "#e0e0e0",
          green: "#8fff8f",
          yellow: "#ffe066",
          red: "#ff7070",
          cyan: "#7fd7ff",
          dim: "#666666",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-tech-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": "0.65rem",
      },
      letterSpacing: {
        tech: "0.15em",
        wider: "0.1em",
      },
    },
  },
  plugins: [],
};
export default config;
