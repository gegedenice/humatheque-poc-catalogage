/** Jetons de la maquette du mode 2 (reference/maquette-mode2-batch.html). */
module.exports = {
  content: ["./src/humatheque/web/templates/**/*.html", "./src/humatheque/web/static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        papier: "#eef0f3", carte: "#ffffff", encre: "#1b2430", sourdine: "#6c7684", filet: "#ced4dc",
        // routages : auto, décision, sortie, conversion, incident
        auto: "#1e9e5a", decision: "#c47f10", sortie: "#d5432f", conversion: "#7a4fb5", incident: "#4a63a8",
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  // Classes construites dynamiquement (couleur de routage) : à conserver.
  safelist: [{ pattern: /(text|bg|border|border-l)-(auto|decision|sortie|conversion|incident|sourdine)/ }],
  plugins: [],
};
