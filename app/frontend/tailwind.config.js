/**
 * Design tokens. One accent (#0072B2, same as the figure palette), neutral grays,
 * danger/success. Tailwind spacing steps of 2 = 8px grid (1 = 4px half-step).
 * Contrast (on white): ink 16:1, ink-muted 8:1, ink-faint 5.6:1, accent 5.2:1,
 * danger 6.6:1, success 6.4:1 -> all WCAG AA for body text.
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Pretendard Variable", "Pretendard", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      /* Hangul wants more leading than Latin and slightly tight tracking. [size, {lineHeight, letterSpacing}] */
      fontSize: {
        caption: ["0.75rem", { lineHeight: "1.125rem", letterSpacing: "0" }],
        small: ["0.8125rem", { lineHeight: "1.25rem", letterSpacing: "-0.005em" }],
        body: ["0.9375rem", { lineHeight: "1.5rem", letterSpacing: "-0.01em" }],
        lead: ["1.0625rem", { lineHeight: "1.75rem", letterSpacing: "-0.012em" }],
        h3: ["1.125rem", { lineHeight: "1.625rem", letterSpacing: "-0.015em" }],
        h2: ["1.375rem", { lineHeight: "2rem", letterSpacing: "-0.02em" }],
        h1: ["1.75rem", { lineHeight: "2.5rem", letterSpacing: "-0.025em" }],
        display: ["2.25rem", { lineHeight: "3rem", letterSpacing: "-0.03em" }],
      },
      colors: {
        ink: { DEFAULT: "#14171c", muted: "#4a515c", faint: "#626a76" },
        line: { DEFAULT: "#d9dde3", strong: "#b4bac4" },
        surface: { DEFAULT: "#ffffff", sunken: "#f4f5f7", inset: "#eceef1" },
        accent: { DEFAULT: "#0072B2", strong: "#005a8c", wash: "#e6f1f8" },
        baseline: "#858c98",
        danger: { DEFAULT: "#b3261e", wash: "#fbeceb" },
        success: { DEFAULT: "#1d6b3a", wash: "#e8f3ec" },
      },
      borderRadius: { DEFAULT: "2px", md: "4px" },
      maxWidth: { page: "72rem", prose: "40rem" },
      keyframes: {
        rise: { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "none" } },
        pulse2: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.5" } },
      },
      animation: { rise: "rise 160ms ease-out", pulse2: "pulse2 1.4s ease-in-out infinite" },
    },
  },
  plugins: [],
};
