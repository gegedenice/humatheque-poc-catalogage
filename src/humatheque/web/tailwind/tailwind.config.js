/** Jetons Material 3 de l'interface (maquettes validées le 05/11, proposition A). */
const routage = (point, fonce, clair) => ({ DEFAULT: point, fonce, clair });
module.exports = {
  content: ["./src/humatheque/web/templates/**/*.html", "./src/humatheque/web/static/js/**/*.js",
            "./src/humatheque/web/__init__.py"],
  theme: {
    extend: {
      colors: {
        // surfaces et texte
        papier: "#f7f8fd", rail: "#eef0f8", carte: "#ffffff", tonal: "#f3f5fc",
        encre: "#1a1c22", sourdine: "#44474f", contour: "#74777f", filet: "#e1e3ec",
        // couleur principale et conteneur
        primaire: { DEFAULT: "#3554a5", fonce: "#1f3b82", clair: "#dce1ff" },
        // routages : point (texte sur blanc), texte foncé, conteneur
        auto: routage("#1b7f4c", "#0f5132", "#d5f5e1"),
        decision: routage("#8a5a00", "#6b4400", "#ffe3b0"),
        sortie: routage("#b3261e", "#8c1d18", "#f9dedc"),
        conversion: routage("#6b47ae", "#4f2d86", "#eaddff"),
        incident: routage("#3f5db0", "#213d78", "#d8e2ff"),
      },
      fontFamily: {
        sans: ['"Roboto Flex"', 'Roboto', 'ui-sans-serif', 'system-ui', '-apple-system', '"Segoe UI"', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['"Roboto Mono"', 'ui-monospace', '"SF Mono"', 'Menlo', 'Consolas', '"DejaVu Sans Mono"', '"Liberation Mono"', 'monospace'],
      },
      borderRadius: { carte: "28px", tuile: "20px" },
      boxShadow: { carte: "0 1px 2px rgba(26,28,34,0.08)" },
    },
  },
  // Classes construites dynamiquement (couleur de routage) : à conserver.
  safelist: [{ pattern: /(text|bg|border|border-l)-(primaire|auto|decision|sortie|conversion|incident|sourdine)(-(fonce|clair))?/ }],
  plugins: [],
};
