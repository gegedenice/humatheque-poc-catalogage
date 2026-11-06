// Îlot « éditeur de règles » (SPEC §9.3, page Règles) : Monaco sur un fichier YAML de regles/.
// Le texte est édité tel quel (commentaires conservés) et recopié dans le champ du
// formulaire, qui reste la source envoyée (Vérifier / Enregistrer fonctionnent sans JavaScript).
(function () {
  "use strict";
  var BASE = "/static/vendor/monaco/vs";
  var instances = new Map();
  var declare = false;

  function chargerMonaco(suite) {
    if (window.monaco) { suite(); return; }
    window.MonacoEnvironment = window.MonacoEnvironment ||
      { getWorkerUrl: function () { return BASE + "/base/worker/workerMain.js"; } };
    window.require.config({ paths: { vs: BASE }, "vs/nls": { availableLanguages: { "*": "fr" } } });
    window.require(["vs/editor/editor.main"], suite);
  }

  function declarer() {
    if (declare) return;
    declare = true;
    monaco.languages.register({ id: "regles-yaml" });
    monaco.languages.setLanguageConfiguration("regles-yaml", {
      comments: { lineComment: "#" },
      brackets: [["{", "}"], ["[", "]"]],
      autoClosingPairs: [{ open: "{", close: "}" }, { open: "[", close: "]" }, { open: '"', close: '"' }, { open: "'", close: "'" }],
    });
    monaco.languages.setMonarchTokensProvider("regles-yaml", {
      tokenizer: {
        root: [
          [/#.*$/, "comment"],
          [/[\w.-]+(?=\s*:)/, "cle"],
          [/"/, { token: "chaine", next: "@chaine2" }],
          [/'/, { token: "chaine", next: "@chaine1" }],
          [/\|\|/, "repli"],
          [/\{\w+(\.\w+)?\}/, "variable"],
          [/\$[0-9A-Za-z]/, "code"],
          [/\b\d{3}\b/, "etiquette"],
        ],
        chaine2: [[/\{\w+(\.\w+)?\}/, "variable"], [/\$[0-9A-Za-z]/, "code"], [/\|\|/, "repli"],
                  [/[^"{$|]+/, "chaine"], [/"/, { token: "chaine", next: "@pop" }], [/./, "chaine"]],
        chaine1: [[/[^']+/, "chaine"], [/'/, { token: "chaine", next: "@pop" }]],
      },
    });
    monaco.editor.defineTheme("regles", {
      base: "vs", inherit: true,
      rules: [
        { token: "comment", foreground: "6c7684", fontStyle: "italic" },
        { token: "cle", foreground: "3554a5", fontStyle: "bold" },
        { token: "chaine", foreground: "1a1c22" },
        { token: "variable", foreground: "6b47ae", fontStyle: "bold" },
        { token: "code", foreground: "b3261e", fontStyle: "bold" },
        { token: "etiquette", foreground: "3554a5" },
        { token: "repli", foreground: "8a5a00", fontStyle: "bold" },
      ],
      colors: { "editor.background": "#ffffff" },
    });
  }

  function initialiser(racine) {
    var el = (racine.id === "editeur-regles") ? racine : racine.querySelector && racine.querySelector("#editeur-regles");
    if (!el || instances.has(el) || typeof window.require === "undefined") return;
    var champ = document.getElementById(el.dataset.cible);
    if (!champ) return;
    instances.set(el, null);
    chargerMonaco(function () {
      declarer();
      champ.hidden = true;
      el.hidden = false;
      var editeur = monaco.editor.create(el, {
        value: champ.value, language: "regles-yaml", theme: "regles", automaticLayout: true,
        minimap: { enabled: false }, fontSize: 13, tabSize: 2, insertSpaces: true, wordWrap: "on",
        renderWhitespace: "boundary", scrollBeyondLastLine: false,
      });
      editeur.onDidChangeModelContent(function () { champ.value = editeur.getValue(); });
      instances.set(el, editeur);
    });
  }

  function detruire(cible) {
    instances.forEach(function (editeur, el) {
      if (cible && cible.contains && cible.contains(el)) {
        if (editeur) editeur.dispose();
        instances.delete(el);
      }
    });
  }

  document.addEventListener("htmx:load", function (e) { initialiser(e.detail.elt); });
  document.addEventListener("htmx:beforeSwap", function (e) { detruire(e.detail.target); });
  document.addEventListener("DOMContentLoaded", function () { initialiser(document.body); });
})();
