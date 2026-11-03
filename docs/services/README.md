Copies de référence des contrats des services externes (lecture seule),
relevées le 03/11/2026 sur `https://{service}.smartbiblia.fr/openapi.json`
et dans les dépôts `gegedenice/humatheque-{sudoc,alma}-checker-api` et
`gegedenice/humatheque-idref-qualinka-api` (idref-linker).
Elles servent à écrire les clients (`services/`) et les mocks `respx`.

Constats :
- alma-checker et sudoc-checker ne typent pas leurs réponses dans l'OpenAPI :
  les statuts et la forme des réponses viennent des README.
- Aucun des deux n'a de statut `error` : une erreur est une réponse HTTP
  4xx/5xx (400 si la requête SRU ne peut pas être construite) ou un service
  injoignable.
- Seuils par défaut des services : doublon Sudoc 0.78 / ambigu 0.62
  (`SUDOC_DUPLICATE_THRESHOLD`, `SUDOC_AMBIGUOUS_THRESHOLD`) ; Alma 0.78 / 0.62.
- idref-linker : statuts accepted / ambiguous / low_confidence / not_found,
  PPN seulement pour accepted, pas de statut `error` ; `/align/person` peut
  dépasser une minute (IDREF_TIMEOUT), `/align/thesis-organizations` est local
  et rapide. `first_name` / `last_name` ne sont pas envoyés (surcharges
  réservées aux découpages incertains).
- dewey-classifier : pas de statut ; `au_dessus_seuil` / `sous_seuil` sont
  calculés par le démonstrateur (DEWEY_SEUIL).
- Notices Sudoc : lues en UNIMARC XML, converties et conservées en ISO 2709
  (pymarc).
- Pas de clé d'API utilisée (authentification prévue, optionnelle, en-tête `X-API-Key`).
