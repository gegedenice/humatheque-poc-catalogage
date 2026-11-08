# humatheque-catalogage — démonstrateur du workflow de catalogage assisté

Application web qui prend un document de thèse ou de mémoire (une ou plusieurs
images, téléversées, par URL ou depuis le bucket MinIO) et produit une notice
UNIMARC, ou s'arrête avec un rapport expliquant pourquoi.
Les règles de catalogage ne sont PAS dans le code : elles sont dans `regles/`
(YAML écrits par le studio de catalogage : `variables.yaml` ; `decisions.yaml`
avec le dictionnaire des faits — codes d'API → valeurs — et le tableau de
décision `quand` ; `verifications.yaml`, états de vérification humaine ;
`services.yaml`, sorties d'IdRef, Dewey et de l'extraction ; `notice/`, socle
et variantes de la notice en zones et sous-zones structurées). Spécification fonctionnelle
complète : `docs/SPEC.md`. La lire avant toute modification. Les exigences du
wiki projet (ING-*, VLM-*, ALM-SUD-*) y sont tracées au §12 : citer leur
identifiant dans les tests et les messages de commit qui les couvrent.

## Principes non négociables

1. **Aucune règle UNIMARC codée en dur.** Les zones, sous-zones, valeurs fixes et
   codes de fonction viennent de `regles/notice/` (socle + variantes) ; la décision et
   l'issue (terminé, à reprendre, incident) viennent de `regles/decisions.yaml`
   et de `regles/services.yaml`. Si une
   règle manque, on la signale (vérificateur, `check-spec`), on ne l'invente pas.
2. **Ne jamais inventer un PPN, un code ou une valeur.** Une donnée absente donne
   une zone omise + une alerte, jamais une valeur plausible.
3. **Le contexte est le contrat.** Tout ce qui est en amont (extraction,
   services, normalisation) produit un dict `Contexte` ; le générateur n'en lit
   que les variables de `regles/variables.yaml`.
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
  `pydantic` v2, `pymarc`, PyYAML, `pytest` + `respx`. Pas d'expressions dans
  les règles : tables de décision et objets de zone, interprétés en Python.
- **Couche de services** (`application/`) : toute logique passe par elle ; les
  pages HTML et l'API JSON `/api/v1` l'appellent toutes les deux. Rien de
  métier dans les routes ni les gabarits.
- **Design** : Material 3 (maquettes validées le 05/11 : palette, cartes, puces) ;
  soigner états vides, chargement, erreurs ; mêmes composants sur tous les
  écrans.
- `uv sync` · `uv run pytest` · `uv run humatheque serve` (port 8000)
  · `uv run humatheque check-spec` · `uv run humatheque css --watch`
  · `docker compose up -d`.
- Code et commentaires en français, identifiants en français ou anglais mais
  cohérents par module. Pas de dépendance lourde sans justification.

## Repères

- `reference/poc-atelier.zip` : POC Gradio actuel. Réutiliser (en copiant et en
  retirant tout `gradio`) : `providers.py` (VLM, images), `prompts.py`
  (`build_eval_prompt`), `services.py` (payloads sudoc / idref / dewey),
  `metadata.normalize_metadata`, vocabulaires de `config.py`.
  Ne pas reprendre : onglets, annotations, export HF, `unimarc.py`, `specs.py`.
- `reference/gradio_workflow_app.py` : autre POC Gradio en mode workflow. Comprend toutes les étapes dans l'ordre.
- `reference/generateur_unimarc.py` : moteur de gabarits de départ (historique).
- `reference/moteur-regles/` : moteur de règles YAML de référence (décision,
  génération, vérification), exemple de contexte et tests sur les règles réelles.
- `reference/workflow-mindmap.svg` : workflow cible validé en atelier.
- `reference/maquette-mode2-batch.html` : maquette du reporting de lot (style et
  structure à reprendre).
