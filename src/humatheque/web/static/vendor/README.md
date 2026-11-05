Bibliothèques vendorisées (sans build, SPEC §9.1) :

- `htmx.min.js` : htmx 2.0.4 (npm `htmx.org`), licence 0BSD (`htmx.LICENSE`).
- `openseadragon/` : OpenSeadragon 5.0.1 (npm `openseadragon`), licence BSD
  3 clauses (`openseadragon/LICENSE.txt`), avec ses images de boutons.
- `monaco/` : Monaco Editor 0.52.2 (npm `monaco-editor`), licence MIT
  (`monaco/LICENSE`). Distribution `min` réduite à `vs/loader.js`,
  `vs/editor/`, `vs/base/` et `vs/nls.messages.fr.js` : les services de
  langage (`vs/language/`) et les langages de base (`vs/basic-languages/`)
  sont retirés, la coloration WinIBW étant un langage Monarch propre
  (`js/editeur.js`).

La feuille `../css/app.css` est compilée par l'exécutable autonome Tailwind
(`uv run humatheque css`, version figée dans `cli.py`), sans Node.
