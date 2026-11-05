# humatheque-catalogage

Démonstrateur du workflow de catalogage assisté des thèses et mémoires : à
partir d'une ou plusieurs images d'un document, il produit une notice UNIMARC
éditable, ou s'arrête avec un rapport expliquant pourquoi. Les règles de
catalogage ne sont pas dans le code : elles sont lues dans la spécification
Grist (`spec/*.csv` ou API Grist).

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
| `humatheque check-spec [--source csv\|grist] [--json]` | vérifie la spécification ; code retour 1 si erreur bloquante |
| `humatheque spec-export [--dossier spec]` | instantané des tables Grist dans `spec/` + copie dans `spec/archives/<horodatage>/` |
| `humatheque spec-import [--nouveau NOM \| --doc ID] [--remplacer]` | construit le classeur Grist depuis `spec/*.csv`, puis le relit et le compare aux CSV |
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
- **Spécification** : `SPEC_SOURCE` (`csv` par défaut, ou `grist`), `GRIST_URL`,
  `GRIST_DOC_ID`, `GRIST_API_KEY`.
- **Déclencheurs automatiques** (valeurs initiales, ensuite réglées dans
  `/admin`) : `INGESTION_AUTO` (balayage périodique), `BALAYAGE_INTERVALLE`
  (minutes), `PLANIFICATION_CRON`, `EVENEMENTS_DELAI` (secondes).
- **Mot de passe** : `MOT_DE_PASSE` (vide : pas d'authentification).

## Administration

La page `/admin` affiche la santé des services, les réglages des lots
automatiques (fournisseur VLM, paramètres avancés) et les trois déclencheurs :
balayage périodique, traitement à heure fixe (cron) et évènements S3. Pour les
évènements, déclarer dans les notifications du bucket (webhook, évènements
`s3:ObjectCreated:*`) l'URL affichée, qui porte un jeton. Le planificateur
tourne dans l'application (`humatheque serve`) : une seule instance.

Les règles métier lues hors de la spec sont dans `config/*.yaml` (états Sudoc
et Alma, provenance, listes de civilités et d'articles, prompts d'extraction…).

## Docker Compose (application + Grist)

```bash
cp .env.example .env      # renseigner aussi GRIST_SESSION_SECRET et GRIST_DEFAULT_EMAIL
docker compose up -d      # application : http://localhost:8000, Grist : http://localhost:8484
```

- `app` est construite depuis le `Dockerfile` (`python:3.12-slim`, uv, cache
  de réaccentuation et CSS construits au build) ; données dans `./data`.
- `grist` : image officielle `gristlabs/grist`, données dans `./grist-data`.
- L'application fonctionne sans Grist (`SPEC_SOURCE=csv`) ; elle joint Grist
  par `http://grist:8484`.
- Derrière un proxy qui re-chiffre TLS, fournir son certificat au build :
  `docker build --secret id=ca,src=/chemin/ca.crt .`

### Initialisation de Grist (une fois)

1. Ouvrir http://localhost:8484/login : en l'absence de système
   d'authentification, Grist connecte l'utilisateur `GRIST_DEFAULT_EMAIL`.
2. Créer une clé d'API : menu du profil → *Profile settings* → *API Key*, et
   la reporter dans `.env` (`GRIST_API_KEY=…`), puis `docker compose up -d app`.
3. Construire le classeur depuis `spec/*.csv` (pas d'import manuel : Grist
   renomme la colonne réservée `id` et devine les types, `008` devenant `8`) :

   ```bash
   docker compose exec app uv run --no-sync humatheque spec-import --nouveau "Spécification humatheque"
   ```

   Chaque CSV devient une table aux colonnes Texte ; l'identifiant de colonne
   est rendu sûr (`id` → `id_spec`, `cardinalité` → `cardinalite`) et le
   libellé garde l'en-tête exact, que lit l'application. Les tables sont
   relues et comparées aux CSV ; la commande affiche l'identifiant du document.
   `--doc ID` complète un document existant, `--remplacer` recrée ses tables.
4. Dans `.env` : `GRIST_DOC_ID=…` (affiché à l'étape 3), `SPEC_SOURCE=grist`,
   puis `docker compose up -d app`.
5. Contrôle : `docker compose exec app uv run --no-sync humatheque check-spec`
   doit donner le même résultat qu'en mode `csv` ; `spec-export` fige une
   version de la spec dans `spec/` (à versionner).

Si Grist est injoignable, l'application se replie sur `spec/*.csv` et
l'affiche sur la page d'accueil. Le bouton « Recharger la spec » relit la
source sans redémarrer.

Note : `GRIST_IN_SERVICE=true` (dans `docker-compose.yml`) évite l'assistant
`/boot` de Grist pour cette instance locale mono-utilisateur ; une instance
exposée sur un réseau doit configurer un vrai système d'authentification.

## Organisation du code

```
src/humatheque/
  config.py          réglages (.env)            cli.py        ligne de commande
  spec/              chargement CSV / Grist, vérificateur, spec-export
  texte_unimarc/     module NOR : réaccentuation, casse, personnes, titre de classement
  extraction/        VLM multi-images, fournisseurs interchangeables
  contexte/          extraction → Contexte (clés = Sources)
  services/          clients alma, sudoc, idref, dewey
  qualification/     agrégation des thesis_state
  decision/          provenance, moteur Situations / Statuts_services
  conversion/        notice Sudoc existante (copie en v1, ISO 2709)
  generateur/        moteur des Regles, WinIBW et MARCXML
  workflow/          orchestrateur : un document → un dossier, reprises
  ingestion/         stockage S3, constitution des documents
  notice/            notice éditable : analyse, validation, versions
  store/             SQLite
  application/       couche de services (pages HTML et API JSON)
  web/               FastAPI, gabarits Jinja2 + HTMX, statiques vendorisés
```
