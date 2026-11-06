# humatheque-catalogage

Démonstrateur du workflow de catalogage assisté des thèses et mémoires : à
partir d'une ou plusieurs images d'un document, il produit une notice UNIMARC
éditable, ou s'arrête avec un rapport expliquant pourquoi. Les règles de
catalogage ne sont pas dans le code : elles sont dans `regles/` (fichiers YAML
lisibles et modifiables depuis l'interface, conditions au format rule-engine).

- Spécification fonctionnelle : [`docs/SPEC.md`](docs/SPEC.md)
- Consignes de développement : [`CLAUDE.md`](CLAUDE.md)
- Contrats des services externes : [`docs/services/`](docs/services/)
- Essai réel : [`docs/ESSAI.md`](docs/ESSAI.md)

## Démarrage rapide (sans Docker)

Prérequis : [uv](https://docs.astral.sh/uv/) (Python 3.12 est installé par uv).

```bash
cp .env.example .env      # puis renseigner les URL et clés (voir plus bas)
uv sync
uv run humatheque check-spec
uv run humatheque serve   # http://127.0.0.1:8000
```

Le premier traitement construit le cache du dictionnaire de réaccentuation
(environ 15 s) dans `data/` ; `uv run humatheque cache-reaccentue` le fait à
l'avance.

## Commandes

| Commande | Rôle |
|---|---|
| `humatheque serve [--host --port]` | interface web, API JSON `/api/v1` (documentation : `/api/v1/docs`) et planificateur des déclencheurs |
| `humatheque check-spec [--dossier regles] [--json]` | vérifie les règles (`regles/*.yaml`) ; code retour 1 si erreur bloquante |
| `humatheque run-url URL… [--un-document] [--type these\|memoire] [--notice]` | traite des URL d'images dans le terminal |
| `humatheque run-batch fichier.txt [--type …] [--notice]` | traite un fichier d'URL (format `minio_urls.txt`, suffixe « FAIT » ignoré) |
| `humatheque scan-minio [--lancer-nouveaux]` | liste les dossiers du bucket S3 et leur dernier statut |
| `humatheque css [--watch]` | compile la feuille Tailwind (exécutable autonome, téléchargé dans `.cache/`) |
| `humatheque cache-reaccentue` | construit le cache du dictionnaire de réaccentuation |
| `uv run pytest` | tests (services simulés par respx, aucun appel réseau) |

## Configuration (`.env`)

`.env.example` liste toutes les variables (valeurs factices). L'essentiel :

- **VLM** : `VLM_FOURNISSEUR` (`vlm`, `openai`, `hf`, `albert`) et l'URL, le
  modèle et la clé du fournisseur choisi (`HF_API_URL`, `HF_MODEL`,
  `HF_TOKEN`… ; `VLM_MODEL` pour le point d'accès `vlm`) ;
  `VLM_STRATEGIE` (`requete_unique` ou `par_image`).
- **Services** : `ALMA_CHECKER_API_URL`, `SUDOC_CHECKER_API_URL`,
  `IDREF_QUALINKA_API_URL`, `DEWEY_CLASSIFICATION_API_URL` (clés facultatives) ;
  `IDREF_TIMEOUT` ; `DEWEY_SEUIL` (vide : première classe retenue). Les
  paramètres de requête des services (seuils dont `duplicate_threshold`
  Sudoc, nombres de candidats, poids IdRef, méthode Dewey) sont dans
  `config/services.yaml`.
- **Stockage S3** (MinIO ou tout service compatible S3) : `MINIO_ENDPOINT`,
  `MINIO_BUCKET`, `MINIO_PREFIXE`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`. Un
  dossier = un document ; le type vient du dossier parent (`TH`/`MM`,
  `theses`/`memoires` : `config/ingestion.yaml`).
- **Règles** : `REGLES_DIR` (`regles` par défaut).
- **Déclencheurs automatiques** (valeurs initiales, ensuite réglées dans
  `/admin`) : `INGESTION_AUTO` (balayage périodique), `BALAYAGE_INTERVALLE`
  (minutes), `PLANIFICATION_CRON`, `EVENEMENTS_DELAI` (secondes).
- **Mot de passe** : `MOT_DE_PASSE` (vide : pas d'authentification).

## Règles de catalogage (`regles/`)

| Fichier | Contenu |
|---|---|
| `regles/variables.yaml` | variables utilisables dans les profils (source, type, liste, disponible) |
| `regles/profils/*.yaml` | notice-modèle de chaque profil : une ligne = une zone écrite comme dans WinIBW (`ligne \|\| repli`, `si`, `joindre`…) |
| `regles/decisions.yaml` | faits, règles de décision (conditions rule-engine, première vraie), `sinon` |

Format détaillé : `docs/SPEC.md` §8 ; référence : `reference/moteur-regles/`.
`humatheque check-spec` les vérifie (erreurs, avertissements, cas non couverts).

**Issue d'un document** : Terminé (rien à faire : notice produite ou convertie,
ou notice déjà au catalogue), À reprendre (un catalogueur tranche, onglet
« Reprise » du dossier) ou Incident (panne technique). Elle découle de l'action
de la règle de décision retenue (`création`, `conversion`, `arrêt` → terminé ;
`vérification` → à reprendre) et des réponses des services, dont l'issue par
statut est dans `config/services.yaml` (`statuts`).

**Administration › Règles** (`/admin/regles`) : tableau des décisions et banc
d'essai (faits saisis ou repris d'un dossier : règle retenue, évaluation de
chaque condition), profils ligne à ligne avec aperçu de la notice, variables, et
modification des fichiers YAML dans l'éditeur (texte et commentaires
conservés). « Vérifier » lance le vérificateur sans rien écrire ; « Enregistrer »
refuse toute erreur bloquante, archive la version précédente dans
`regles/archives/` (non versionné), journalise (`data/journal_regles.jsonl`) et
recharge les règles. Les fichiers modifiés sont ceux du dépôt (volume
`./regles` en Docker) : pensez à les commiter.

## Administration

La page `/admin` (« Services et déclencheurs ») affiche la santé des services, les réglages des lots
automatiques (fournisseur VLM, paramètres avancés) et les trois déclencheurs :
balayage périodique, traitement à heure fixe (cron) et évènements S3. Pour les
évènements, déclarer dans les notifications du bucket (webhook, évènements
`s3:ObjectCreated:*`) l'URL affichée, qui porte un jeton. Le planificateur
tourne dans l'application (`humatheque serve`) : une seule instance.

Les règles métier lues hors de `regles/` sont dans `config/*.yaml` (états
Sudoc et Alma, provenance, listes de civilités et d'articles, prompts
d'extraction, conventions de lecture des règles…).

## Docker Compose

```bash
cp .env.example .env
docker compose up -d      # application : http://localhost:8000
```

- `app` est construite depuis le `Dockerfile` (`python:3.12-slim`, uv, cache
  de réaccentuation et CSS construits au build) ; données dans `./data`.
- `./regles` est monté en volume : les modifications faites depuis
  Administration › Règles restent dans le dépôt (à commiter).
- Derrière un proxy qui re-chiffre TLS, fournir son certificat au build :
  `docker build --secret id=ca,src=/chemin/ca.crt .`

## Organisation du code

```
src/humatheque/
  config.py          réglages (.env)            cli.py        ligne de commande
  regles/            chargement des règles YAML, moteur (décision, génération), vérificateur
  texte_unimarc/     module NOR : réaccentuation, casse, personnes, titre de classement
  extraction/        VLM multi-images, fournisseurs interchangeables
  contexte/          extraction → Contexte (clés = Sources)
  services/          clients alma, sudoc, idref, dewey
  qualification/     agrégation des thesis_state
  decision/          provenance, faits et décision (decisions.yaml), issue des réponses de services
  conversion/        notice Sudoc existante (copie en v1, ISO 2709)
  generateur/        remplissage des profils, WinIBW et MARCXML
  workflow/          orchestrateur : un document → un dossier, reprises
  ingestion/         stockage S3, constitution des documents
  notice/            notice éditable : analyse, validation, versions
  store/             SQLite
  application/       couche de services (pages HTML et API JSON)
  web/               FastAPI, gabarits Jinja2 + HTMX, statiques vendorisés
```
