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
