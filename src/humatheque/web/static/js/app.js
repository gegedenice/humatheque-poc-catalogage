// Comportements globaux HTMX (SPEC §9.1) : jamais d'échec silencieux.
(function () {
  "use strict";
  function afficherErreur(message) {
    var bandeau = document.getElementById("bandeau-erreur");
    var texte = document.getElementById("bandeau-erreur-texte");
    if (!bandeau || !texte) return;
    texte.textContent = message;
    bandeau.hidden = false;
  }
  document.addEventListener("htmx:responseError", function (e) {
    var xhr = e.detail.xhr;
    var corps = (xhr.responseText || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    afficherErreur(corps ? corps.slice(0, 400) : "Erreur " + xhr.status + " sur " + e.detail.pathInfo.requestPath);
  });
  document.addEventListener("htmx:sendError", function () {
    afficherErreur("Le serveur ne répond pas. Vérifiez qu'il est démarré, puis réessayez.");
  });
  document.addEventListener("htmx:beforeRequest", function () {
    var bandeau = document.getElementById("bandeau-erreur");
    if (bandeau) bandeau.hidden = true;
  });
})();

// Composants d'interface (Material 3) : onglets, liste des fichiers déposés,
// valeur affichée des curseurs. Délégation sur le document : fonctionne aussi
// après une navigation hx-boost ou un rafraîchissement de fragment.
(function () {
  "use strict";
  // onglet choisi par conteneur (id) : réappliqué quand un fragment est remplacé
  var choix = {};

  function activer(onglet) {
    var conteneur = onglet.closest("[data-onglets]");
    if (!conteneur) return;
    conteneur.querySelectorAll('[role="tab"]').forEach(function (o) {
      var actif = o === onglet;
      o.setAttribute("aria-selected", actif ? "true" : "false");
      o.tabIndex = actif ? 0 : -1;
      var volet = document.getElementById(o.getAttribute("aria-controls"));
      if (volet) volet.hidden = !actif;
    });
    if (conteneur.id) choix[conteneur.id] = onglet.id;
  }

  document.addEventListener("click", function (e) {
    var onglet = e.target.closest('[data-onglets] [role="tab"]');
    if (onglet) activer(onglet);
  });
  document.addEventListener("keydown", function (e) {
    var onglet = e.target.closest('[data-onglets] [role="tab"]');
    if (!onglet || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
    var tous = Array.prototype.slice.call(onglet.parentElement.querySelectorAll('[role="tab"]'));
    var i = tous.indexOf(onglet) + (e.key === "ArrowRight" ? 1 : -1);
    var cible = tous[(i + tous.length) % tous.length];
    activer(cible);
    cible.focus();
  });
  document.addEventListener("htmx:load", function (e) {
    e.detail.elt.querySelectorAll("[data-onglets][id]").forEach(function (c) {
      var o = choix[c.id] && document.getElementById(choix[c.id]);
      if (o && c.contains(o)) activer(o);
    });
  });

  // liste des fichiers choisis sous la zone de dépôt
  document.addEventListener("change", function (e) {
    var champ = e.target;
    if (!champ.matches || !champ.matches('input[type="file"]')) return;
    var liste = document.querySelector('[data-liste-fichiers="' + champ.name + '"]');
    if (!liste) return;
    liste.textContent = "";
    var fichiers = Array.prototype.slice.call(champ.files || []);
    fichiers.slice(0, 30).forEach(function (f) {
      var li = document.createElement("li");
      li.className = "puce-contour h-8 text-[13px] font-mono";
      li.textContent = f.webkitRelativePath || f.name;
      liste.appendChild(li);
    });
    if (fichiers.length > 30) {
      var plus = document.createElement("li");
      plus.className = "text-[13px] text-sourdine self-center";
      plus.textContent = "+ " + (fichiers.length - 30) + " autre(s)";
      liste.appendChild(plus);
    }
  });

  // valeur d'un curseur à côté de son libellé
  document.addEventListener("input", function (e) {
    var c = e.target;
    if (!c.matches || !c.matches('input[type="range"]')) return;
    var sortie = c.closest("label") && c.closest("label").querySelector("[data-curseur-valeur]");
    if (sortie) sortie.textContent = Number(c.value).toFixed(2);
  });
})();
