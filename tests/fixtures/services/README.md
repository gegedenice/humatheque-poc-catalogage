Réponses réelles des services, relevées le 03/11/2026 (données publiques
du Sudoc, d'Alma SRU et d'IdRef), servant de base aux mocks `respx` :

- `sudoc.json`, `alma.json` : `/check/thesis` sur l'exemple du README de
  sudoc-checker (thèse de G. Jaelani, EHESS, 2017) ;
- `idref.json` : `/align/person` (« Gérard JORLAND »), statut `low_confidence` ;
- `idreforg.json` : `/align/thesis-organizations` (EPHE, Université de Genève) ;
- `dewey.json` : `/classify` ;
- `sudoc-229969437.xml` : notice UNIMARC du PPN 229969437, obtenue par
  sudoc-checker avec `include_unimarc_xml=true` (même format que
  `https://www.sudoc.fr/229969437.xml`, domaine non joignable depuis
  l'environnement de développement).

Les tests en dérivent des variantes (statuts, thesis_state, scores) en
modifiant des copies, sans toucher à ces fichiers.
