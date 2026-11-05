// Îlot « éditeur de notice » (SPEC §9.2) : Monaco (distribution min, chargeur AMD, sans build).
// Échange en JSON avec /api/v1/notices/{id} ; initialisé sur htmx:load, détruit avant remplacement.
(function () {
  "use strict";
  var BASE = "/static/vendor/monaco/vs";
  var instances = new Map();
  var langageDeclare = false;

  window.MonacoEnvironment = { getWorkerUrl: function () { return BASE + "/base/worker/workerMain.js"; } };

  function chargerMonaco(suite) {
    if (window.monaco) { suite(); return; }
    window.require.config({ paths: { vs: BASE }, "vs/nls": { availableLanguages: { "*": "fr" } } });
    window.require(["vs/editor/editor.main"], suite);
  }

  function declarerLangage() {
    if (langageDeclare) return;
    langageDeclare = true;
    monaco.languages.register({ id: "winibw" });
    monaco.languages.setMonarchTokensProvider("winibw", {
      tokenizer: {
        root: [
          [/^(\d{3})( )([0-9#]{2})(?=\$)/, ["etiquette", "", "indicateurs"]],
          [/^\d{3}/, "etiquette"],
          [/\$[0-9A-Za-z]/, "code"],
          [/[^$]+/, "valeur"],
        ],
      },
    });
    monaco.editor.defineTheme("winibw", {
      base: "vs", inherit: true,
      rules: [
        { token: "etiquette", foreground: "4a63a8", fontStyle: "bold" },
        { token: "indicateurs", foreground: "c47f10" },
        { token: "code", foreground: "d5432f", fontStyle: "bold" },
        { token: "valeur", foreground: "1b2430" },
      ],
      colors: { "editor.background": "#ffffff", "editorLineNumber.foreground": "#6c7684" },
    });
  }

  function json(url, methode, corps) {
    return fetch(url, {
      method: methode || "GET",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: corps ? JSON.stringify(corps) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, statut: r.status, donnees: d }; });
    });
  }

  function initialiser(racine) {
    var el = (racine.id === "editeur-notice") ? racine : racine.querySelector && racine.querySelector("#editeur-notice");
    if (!el || instances.has(el) || typeof window.require === "undefined") return;
    var id = el.dataset.dossier;
    var etat = document.getElementById("editeur-etat");
    var auteur = document.getElementById("editeur-auteur");
    var statut = document.getElementById("editeur-statut");
    var api = "/api/v1/notices/" + encodeURIComponent(id);
    var ctx = { version: null, enregistre: "", minuterie: null, editeur: null };
    instances.set(el, ctx);

    function message(texte, ton) {
      etat.textContent = texte;
      etat.className = "text-xs " + (ton === "erreur" ? "text-sortie" : ton === "ok" ? "text-auto" : "text-sourdine");
    }

    function marquer(marqueurs) {
      var modele = ctx.editeur.getModel();
      monaco.editor.setModelMarkers(modele, "validation", marqueurs.map(function (m) {
        var fin = Math.max(m.colonne_fin, m.colonne_debut + 1);
        if (m.colonne_fin <= 1) fin = modele.getLineMaxColumn(Math.min(m.ligne, modele.getLineCount()));
        return {
          startLineNumber: m.ligne, endLineNumber: m.ligne, startColumn: m.colonne_debut, endColumn: fin,
          severity: m.gravite === "erreur" ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
          message: m.message,
        };
      }));
      var erreurs = marqueurs.filter(function (m) { return m.gravite === "erreur"; }).length;
      var avert = marqueurs.length - erreurs;
      var modifie = ctx.editeur.getValue() !== ctx.enregistre;
      message((erreurs ? erreurs + " erreur(s)" : "aucune erreur") + " · " + avert + " avertissement(s)"
              + (modifie ? " · modifications non enregistrées" : ""), erreurs ? "erreur" : "");
    }

    function valider() {
      json(api + "/validation", "POST", { texte: ctx.editeur.getValue() }).then(function (r) {
        if (r.ok) marquer(r.donnees);
      });
    }

    function appliquer(vue) {
      ctx.version = vue.version;
      ctx.enregistre = vue.texte;
      statut.textContent = vue.version ? "version " + vue.version + " · " + vue.statut : "aucune version";
    }

    function avecAuteur() {
      if (!auteur.value.trim()) { message("Indiquez votre nom (catalogueur) avant d'enregistrer.", "erreur"); auteur.focus(); return false; }
      return true;
    }

    function enregistrer() {
      if (!avecAuteur()) return Promise.resolve(false);
      if (ctx.editeur.getValue() === ctx.enregistre) { message("Aucune modification à enregistrer.", ""); return Promise.resolve(true); }
      return json(api, "PUT", { texte: ctx.editeur.getValue(), auteur: auteur.value.trim(), version: ctx.version }).then(function (r) {
        if (!r.ok) { message(r.donnees.detail || "Enregistrement impossible.", "erreur"); return false; }
        appliquer(r.donnees);
        marquer(r.donnees.marqueurs);
        message("Version " + r.donnees.version + " enregistrée.", "ok");
        htmx.trigger(document.body, "notice-enregistree");
        return true;
      });
    }

    function validerNotice() {
      enregistrer().then(function (ok) {
        if (!ok) return;
        json(api + "/valider", "POST", { auteur: auteur.value.trim() }).then(function (r) {
          if (!r.ok) {
            if (r.donnees.marqueurs) marquer(r.donnees.marqueurs);
            message((r.donnees.detail || "Validation refusée.") + " Corrigez les erreurs soulignées.", "erreur");
            return;
          }
          appliquer(r.donnees);
          message("Notice validée (version " + r.donnees.version + ").", "ok");
          htmx.trigger(document.body, "notice-enregistree");
        });
      });
    }

    message("Chargement de l'éditeur…", "");
    chargerMonaco(function () {
      if (!instances.has(el)) return;
      declarerLangage();
      json(api).then(function (r) {
        if (!r.ok) { message("Notice indisponible.", "erreur"); return; }
        ctx.editeur = monaco.editor.create(el, {
          value: r.donnees.texte, language: "winibw", theme: "winibw", automaticLayout: true,
          minimap: { enabled: false }, wordWrap: "on", fontSize: 13, lineNumbers: "on",
          scrollBeyondLastLine: false, renderWhitespace: "boundary",
        });
        appliquer(r.donnees);
        marquer(r.donnees.marqueurs);
        ctx.editeur.onDidChangeModelContent(function () {
          clearTimeout(ctx.minuterie);
          ctx.minuterie = setTimeout(valider, 600);
        });
        ctx.editeur.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, enregistrer);
        document.getElementById("editeur-enregistrer").addEventListener("click", enregistrer);
        document.getElementById("editeur-valider").addEventListener("click", validerNotice);
      });
    });
  }

  function detruire(racine) {
    instances.forEach(function (ctx, el) {
      if (racine === el || (racine.contains && racine.contains(el))) {
        clearTimeout(ctx.minuterie);
        if (ctx.editeur) ctx.editeur.dispose();
        instances.delete(el);
      }
    });
  }

  document.addEventListener("htmx:load", function (e) { initialiser(e.detail.elt); });
  document.addEventListener("htmx:beforeSwap", function (e) { detruire(e.detail.target); });
  document.addEventListener("DOMContentLoaded", function () { initialiser(document.body); });
})();
