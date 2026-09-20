/** @type {import('tailwindcss').Config} */
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
    extend: {},
  },
  plugins: [],
};
