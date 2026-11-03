# humatheque-catalogage — démonstrateur du workflow de catalogage assisté

Application web qui prend un document de thèse ou de mémoire (une ou plusieurs
images, téléversées, par URL ou depuis le bucket MinIO) et produit une notice
UNIMARC, ou s'arrête avec un rapport expliquant pourquoi.
Les règles de catalogage ne sont PAS dans le code : elles sont lues dans la
spécification Grist (`spec/*.csv` ou API Grist). Spécification fonctionnelle
complète : `docs/SPEC.md`. La lire avant toute modification. Les exigences du
wiki projet (ING-*, VLM-*, ALM-SUD-*) y sont tracées au §12 : citer leur
identifiant dans les tests et les messages de commit qui les couvrent.

## Principes non négociables

1. **Aucune règle UNIMARC codée en dur.** Les zones, sous-zones, valeurs fixes et
   codes de fonction viennent de `Regles` ; le routage vient de `Situations` et
   `Statuts_services`. Si une règle manque, on la signale (vérificateur de spec),
   on ne l'invente pas.
2. **Ne jamais inventer un PPN, un code ou une valeur.** Une donnée absente donne
   une zone omise + une alerte, jamais une valeur plausible.
3. **Le contexte est le contrat.** Tout ce qui est en amont (extraction,
   services, normalisation) produit un dict `Contexte` conforme à `Sources` ;
   le générateur ne lit que ce dict.
4. **Traçabilité.** Chaque transformation de texte, chaque appel de service,
   chaque décision et chaque zone produite est journalisée dans le dossier du
   document (avant / après, règle appliquée, statut de la règle).
5. **Les services externes ne sont pas réimplémentés** : sudoc-checker,
   alma-checker, idref-linker, dewey-classifier sont appelés en HTTP. Lire leur
   OpenAPI (`{url}/openapi.json`) avant d'écrire un client.

## Pile et commandes

- Python 3.12, `uv`, FastAPI + Jinja2 (HTML rendu côté serveur) + **HTMX**,
  **Tailwind CSS** via l'exécutable autonome (pas de Node), îlots JavaScript
  limités à l'éditeur de notice **Monaco** et à la visionneuse **OpenSeadragon**
  (vendorisés, sans build), SQLite (`data/humatheque.db`), `httpx`,
  `pydantic` v2, `pymarc`, `pytest` + `respx`.
- **Couche de services** (`application/`) : toute logique passe par elle ; les
  pages HTML et l'API JSON `/api/v1` l'appellent toutes les deux. Rien de
  métier dans les routes ni les gabarits.
- **Design** : partir de la maquette du mode 2 (palette, cartes, badges) ;
  soigner états vides, chargement, erreurs ; mêmes composants sur tous les
  écrans.
- `uv sync` · `uv run pytest` · `uv run humatheque serve` (port 8000)
  · `uv run humatheque check-spec` · `uv run humatheque css --watch`
  · `docker compose up -d` (app + Grist).
- Code et commentaires en français, identifiants en français ou anglais mais
  cohérents par module. Pas de dépendance lourde sans justification.

## Repères

- `reference/poc-atelier.zip` : POC Gradio actuel. Réutiliser (en copiant et en
  retirant tout `gradio`) : `providers.py` (VLM, images), `prompts.py`
  (`build_eval_prompt`), `services.py` (payloads sudoc / idref / dewey),
  `metadata.normalize_metadata`, vocabulaires de `config.py`.
  Ne pas reprendre : onglets, annotations, export HF, `unimarc.py`, `specs.py`.
- `reference/gradio_workflow_app.py` : autre POC Gradio en mode workflow. Comprend toutes les étapes dans l'ordre.
- `reference/generateur_unimarc.py` : moteur de gabarits de départ.
- `reference/workflow-mindmap.svg` : workflow cible validé en atelier.
- `reference/maquette-mode2-batch.html` : maquette du reporting de lot (style et
  structure à reprendre).
