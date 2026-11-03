Copies de référence des contrats des services externes (lecture seule),
relevées le 03/11/2026 sur `https://{service}.smartbiblia.fr/openapi.json`
et dans les dépôts `gegedenice/humatheque-{sudoc,alma}-checker-api`.
Elles servent à écrire les clients (`services/`) et les mocks `respx`.

Constats :
- alma-checker et sudoc-checker ne typent pas leurs réponses dans l'OpenAPI :
  les statuts et la forme des réponses viennent des README.
- Aucun des deux n'a de statut `error` : une erreur est une réponse HTTP
  4xx/5xx (400 si la requête SRU ne peut pas être construite) ou un service
  injoignable.
- Seuils par défaut des services : doublon Sudoc 0.78 / ambigu 0.62
  (`SUDOC_DUPLICATE_THRESHOLD`, `SUDOC_AMBIGUOUS_THRESHOLD`) ; Alma 0.78 / 0.62.
- Pas de clé d'API utilisée (authentification prévue, optionnelle, en-tête `X-API-Key`).
