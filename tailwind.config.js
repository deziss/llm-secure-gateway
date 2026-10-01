/** @type {import('tailwindcss').Config} */

// Colour tokens resolve to CSS variables defined in static/css/input.css, so one
// utility (`bg-surface-raised`) is correct in both themes without a `dark:` pair.
// `<alpha-value>` keeps opacity modifiers working: `bg-surface-raised/60`.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  content: [
    "./src/llm_gateway/templates/**/*.html",
    "./src/llm_gateway/templates/**/*.txt",
    "./src/llm_gateway/static/js/**/*.js",
    // Vendored minified libraries contain no Tailwind classes — scanning them
    // only slows the build and injects false positives.
    "!./src/llm_gateway/static/js/**/*.min.js",
  ],
  // Dark mode is toggled by adding `.dark` to <html> (see base.html theme script).
  // Must stay 'class' — 'media' would ignore the in-app theme switcher.
  darkMode: "class",
  theme: {
    extend: {
      // Existing literal slate-*/indigo-* classes are untouched, so pages can be
      // migrated one at a time. New markup should use these.
      colors: {
        "surface-base": token("surface-base"),
        "surface-raised": token("surface-raised"),
        "surface-inset": token("surface-inset"),
        hairline: token("hairline"),
        ink: token("ink"),
        "ink-muted": token("ink-muted"),
        accent: token("accent"),
        "accent-ink": token("accent-ink"),
        ok: token("ok"),
        warn: token("warn"),
        down: token("down"),
      },
      // Replaces the text-[9px] / text-[10px] / text-[11px] arbitrary values
      // (62 uses of text-[10px] alone) with named, line-height-paired steps.
      fontSize: {
        micro: ["0.625rem", { lineHeight: "0.875rem", letterSpacing: "0.02em" }],
        caption: ["0.6875rem", { lineHeight: "1rem" }],
      },
      // Two radii with distinct jobs, replacing the xl/2xl/3xl mix.
      // Avatars and pills keep rounded-full.
      borderRadius: {
        control: "0.5rem", // buttons, inputs, badges
        panel: "0.75rem", // cards, tables, modals
      },
      // Two elevations: resting content and floating overlays.
      boxShadow: {
        rest: "0 1px 2px 0 rgb(0 0 0 / 0.12), 0 0 0 1px rgb(var(--hairline) / 1)",
        overlay: "0 16px 48px -12px rgb(0 0 0 / 0.55), 0 0 0 1px rgb(var(--hairline) / 1)",
      },
      width: { sidebar: "15rem", rail: "4rem" },
      spacing: { sidebar: "15rem", rail: "4rem" },
      minHeight: { touch: "2.75rem" }, // 44px minimum touch target
      minWidth: { touch: "2.75rem" },
      // dvh avoids the iOS Safari URL-bar overscroll bug that 100vh causes.
      height: { screen: "100dvh" },
    },
  },
  plugins: [],
};
