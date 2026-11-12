# humatheque-catalogage

Démonstrateur du workflow de catalogage assisté des thèses et mémoires : à
partir d'une ou plusieurs images d'un document, il produit une notice UNIMARC
éditable, ou s'arrête avec un rapport expliquant pourquoi. Les règles de
catalogage ne sont pas dans le code : elles sont dans `regles/` (fichiers YAML
écrits par le **studio de catalogage** de l'interface : pipeline en arbre de
décision sur les codes des API, notice structurée en zones et sous-zones).

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

## Commandes

| Commande | Rôle |
|---|---|
| `humatheque serve [--host --port]` | interface web, API JSON `/api/v1` (documentation : `/api/v1/docs`) et planificateur des déclencheurs |
| `humatheque check-spec [--dossier regles] [--json]` | vérifie les règles (`regles/*.yaml`) ; code retour 1 si erreur bloquante |
| `humatheque run-url URL… [--un-document] [--type these\|memoire] [--notice]` | traite des URL d'images dans le terminal |
| `humatheque run-batch fichier.txt [--type …] [--notice]` | traite un fichier d'URL (format `minio_urls.txt`, suffixe « FAIT » ignoré) |
| `humatheque scan-minio [--lancer-nouveaux]` | liste les dossiers du bucket S3 et leur dernier statut |
| `humatheque css [--watch]` | compile la feuille Tailwind (exécutable autonome, téléchargé dans `.cache/`) |
| `uv run pytest` | tests (services simulés par respx, aucun appel réseau) |

## Configuration (`.env`)

`.env.example` liste toutes les variables (valeurs factices). L'essentiel :

- **VLM** : `VLM_FOURNISSEUR` (`albert` par défaut, `vlm`, `hf` : modèles
  ouverts uniquement) et l'URL, le modèle et la clé du fournisseur choisi
  (`ALBERT_API_URL`, `ALBERT_MODEL`, `ALBERT_API_KEY`… ; `VLM_MODEL` pour le
  point d'accès `vlm`) ;
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
| `regles/variables.yaml` | données utilisables dans la notice (source, type, liste, disponible) |
| `regles/notice/socle.yaml` | zones communes de la notice : sous-zones configurables (constante, donnée, composition, autorité ; repli ou vérification si absente), rendu des autorités |
| `regles/notice/variantes/*.yaml` | thèse originale, thèse de reproduction, mémoire : zones ajoutées, remplacées, retirées |
| `regles/alertes.yaml` | alertes affichées au-dessus de la notice : libellé et affichage par famille |
| `regles/pipeline.yaml` | pipeline : Alma → provenance → Sudoc → IdRef → Dewey → création (l'ordre du fichier est l'ordre d'exécution, réglable dans le studio à l'intérieur de chaque groupe) ; pour chaque étape, arbre de décision sur les codes renvoyés par l'API (`status`, `thesis_state`), résultats `stop`, `stop à reprendre`, `conversion`, `poursuite` (avec alerte, à reprendre), `création` |

Format détaillé : `docs/SPEC.md` §8 ; référence : `reference/moteur-regles/`.
`humatheque check-spec` les vérifie (erreurs, avertissements, cas qui tombent dans « sinon »).

**Issue d'un document** : Terminé (rien à faire : notice produite ou convertie,
ou notice déjà au catalogue), À reprendre (un catalogueur tranche, onglet
« Reprise » du dossier) ou Incident (panne technique). Elle découle des
résultats du pipeline : `stop`, `conversion`, `création` → terminé ;
`stop à reprendre`, `poursuite à reprendre` → à reprendre.

**Studio de catalogage** (`/admin/regles`) : **Pipeline** (étapes numérotées,
arbre indenté SI / ET / OU / SAUF / SINON, branches modifiables sur place,
étape suivante indiquée), **Notice UNIMARC** (socle et variantes, éditeur de
zones et de sous-zones avec aperçu immédiat), **Banc d'essai** (un document →
parcours du pipeline, données préparées, notice avec provenance ;
non-régression sur les notices déjà produites) et
**Fichiers** (YAML, experts). Chaque enregistrement refuse toute erreur
bloquante du vérificateur, archive la version précédente dans
`regles/archives/` (non versionné), journalise (`data/journal_regles.jsonl`) et
recharge les règles. Les fichiers modifiés sont ceux du dépôt (volume
`./regles` en Docker) : pensez à les commiter.

## Administration

La page `/admin` (« Services et déclencheurs ») affiche la santé des services, les réglages des lots
automatiques (fournisseur VLM, modèle de chaque fournisseur — celui de `.env` ou
un autre choisi dans la liste renvoyée par l'API —, paramètres avancés) et les trois déclencheurs :
balayage périodique, traitement à heure fixe (cron) et évènements S3. Pour les
évènements, déclarer dans les notifications du bucket (webhook, évènements
`s3:ObjectCreated:*`) l'URL affichée, qui porte un jeton. Le planificateur
tourne dans l'application (`humatheque serve`) : une seule instance.

La page `/admin/prompts` affiche et modifie les prompts d'extraction
(`config/prompts/`, archivés et journalisés), avec à côté la correspondance
« champ du prompt → variable de la notice → transformation ». La casse, les
accents et les civilités sont pris en charge par le VLM ; en Python ne restent
que le `@` du titre de classement, la minuscule du sous-titre et le découpage
nom / prénom.

Les autres réglages lus hors de `regles/` sont dans `config/*.yaml` (provenance,
civilités et articles, services…). Les alertes affichées au-dessus des notices
se règlent dans le studio, onglet « Alertes » (`regles/alertes.yaml`).

## Docker Compose

```bash
cp .env.example .env
docker compose up -d      # application : http://localhost:8000
```

- `app` est construite depuis le `Dockerfile` (`python:3.12-slim`, uv, CSS
  construite au build) ; données dans `./data`.
- `./regles` et `./config/prompts` sont montés en volume : les modifications faites depuis
  Administration › Règles restent dans le dépôt (à commiter).
- Derrière un proxy qui re-chiffre TLS, fournir son certificat au build :
  `docker build --secret id=ca,src=/chemin/ca.crt .`

### Sur un VPS, dans une stack Traefik

`deploy/vps/` contient un service prêt à ajouter à une stack existante
(Traefik + Let's Encrypt, réseau Docker `web`, services humathèque, MinIO et
VLM llama.cpp dans le même compose) :

- `compose.catalogage.yml` : le service `catalogage` (build depuis le clone,
  labels Traefik, volumes `data/`, `regles/`, `config/prompts/`) ; domaine
  à adapter ;
- `catalogage.env.example` : variables de l'application avec les **adresses
  internes** du réseau `web` (`http://sudoc-checker:8000`,
  `http://minio:9000`, `http://llama-server-qwen:8080/v1/chat/completions`…) :
  les appels ne repassent ni par Traefik ni par Internet. Fichier distinct
  du `.env` de la stack ; valeurs factices, à renseigner.

```bash
# dans le dossier du compose de la stack
git clone -b <branche> <url-du-depot> humatheque-poc-catalogage
cp humatheque-poc-catalogage/deploy/vps/catalogage.env.example .catalogage.env   # puis renseigner
# coller le service de deploy/vps/compose.catalogage.yml dans compose.yml, ou :
docker compose -f compose.yml -f humatheque-poc-catalogage/deploy/vps/compose.catalogage.yml up -d --build catalogage
```

- Exposée sur Internet : renseigner `MOT_DE_PASSE` (sinon l'application est ouverte).
- Mise à jour : `git pull` dans le clone puis `up -d --build catalogage` ;
  la base et les dossiers (`data/`) sont conservés. Les règles et prompts
  modifiés depuis l'administration sont dans le clone : lancer
  `scripts/sauvegarde-regles.sh` (ci-dessous) avant un `git pull`.
- Évènements MinIO (déclencheur « évènements ») : webhook vers
  `http://catalogage:8000/minio/evenements?jeton=…` (adresse interne ; jeton
  affiché dans /admin).

#### Sauvegarde nocturne des règles (cron sur l'hôte)

Les règles (`regles/`) et les prompts (`config/prompts/`) modifiés depuis
l'application sont écrits dans le clone du VPS. `scripts/sauvegarde-regles.sh`
les commite (rien d'autre : ni `data/`, ni les archives) et les pousse vers la
branche **`regles-vps`**, à fusionner dans `main` par une PR (fusion classique,
sans squash, pour que les pushs suivants restent en avance rapide). Sans
modification, il ne fait rien ; si le push échoue, le commit reste local et
part au passage suivant.

```bash
# crontab -e (compte propriétaire du clone, qui a l'accès en écriture au dépôt) : chaque nuit à 2 h 47
47 2 * * * /chemin/vers/humatheque-poc-catalogage/scripts/sauvegarde-regles.sh >> /chemin/vers/sauvegarde-regles.log 2>&1
```

- Accès en écriture : une clé de déploiement SSH du dépôt, avec droit
  d'écriture, configurée pour ce compte (`git remote` en SSH). Aucun secret
  dans le dépôt ni dans le script.
- Variables facultatives : `BRANCHE` (défaut `regles-vps`), `DISTANT`
  (défaut `origin`), `AUTEUR` des commits.
- Ne pas modifier `regles/` directement dans `main` pendant que le studio du
  VPS est utilisé : fusionner d'abord `regles-vps`.

## Organisation du code

```
src/humatheque/
  config.py          réglages (.env)            cli.py        ligne de commande
  regles/            chargement des règles YAML, décision (tables), notice structurée, vérificateur
  texte_unimarc/     titre de classement (@), minuscule du sous-titre, nom / prénom
  extraction/        VLM multi-images, fournisseurs interchangeables
  contexte/          extraction → Contexte (clés = Sources)
  services/          clients alma, sudoc, idref, dewey
  qualification/     agrégation des thesis_state
  decision/          provenance, valeurs lues par le pipeline (pipeline.yaml), décision
  conversion/        notice Sudoc existante (copie en v1, ISO 2709)
  generateur/        notice d'un dossier (socle + variante) ; exports ISO 2709 et UNIMARC/XML
  workflow/          orchestrateur : un document → un dossier, reprises
  ingestion/         stockage S3, constitution des documents
  notice/            notice éditable : analyse, validation, versions
  store/             SQLite
  application/       couche de services (pages HTML et API JSON)
  web/               FastAPI, gabarits Jinja2 + HTMX, statiques vendorisés
```
