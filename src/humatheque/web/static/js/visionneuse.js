// Îlot « visionneuse » (SPEC §9.1) : OpenSeadragon, images du document en séquence.
// Initialisé sur htmx:load, détruit proprement avant tout remplacement qui le contient.
(function () {
  "use strict";
  var instances = new Map();

  function initialiser(racine) {
    var el = (racine.id === "visionneuse") ? racine : racine.querySelector && racine.querySelector("#visionneuse");
    if (!el || instances.has(el) || typeof OpenSeadragon === "undefined") return;
    var images = JSON.parse(el.dataset.images || "[]");
    if (!images.length) return;
    var viewer = OpenSeadragon({
      element: el,
      prefixUrl: "/static/vendor/openseadragon/images/",
      tileSources: images.map(function (url) { return { type: "image", url: url }; }),
      sequenceMode: images.length > 1,
      showReferenceStrip: images.length > 1,
      showNavigator: true,
      gestureSettingsMouse: { clickToZoom: false },
    });
    instances.set(el, viewer);
  }

  function detruire(racine) {
    instances.forEach(function (viewer, el) {
      if (racine === el || (racine.contains && racine.contains(el))) {
        viewer.destroy();
        instances.delete(el);
      }
    });
  }

  document.addEventListener("htmx:load", function (e) { initialiser(e.detail.elt); });
  document.addEventListener("htmx:beforeSwap", function (e) { detruire(e.detail.target); });
  document.addEventListener("DOMContentLoaded", function () { initialiser(document.body); });
})();
