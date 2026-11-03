# Spécification — démonstrateur humatheque-catalogage (v1)

## 1. Objet et périmètre

Enchaîner automatiquement, pour un document de thèse ou de mémoire (une ou
plusieurs images : page de titre, résumé…) :
extraction → normalisation → vérifications Alma et Sudoc → situation → (si
création) alignements IdRef + Dewey → notice UNIMARC, avec un rapport par
document et par lot.

La notice produite (créée ou convertie) est **éditable** dans l'interface
avant export (§9.2).

Deux modes sur le même moteur :
- **Mode 1, document unique** (optionnel) : on suit le dossier étape par étape et on tranche
  les exceptions dans la foulée.
- **Mode 2, lot** (mode préféré de l'équipe projet, à présenter prioritairement) : liste d'images traitée en
  tâche de fond, puis reporting et file d'exceptions (cf. maquette).

**Hors v1** : règles de conversion des notices Sudoc existantes (en v1, la
notice source est reprise telle quelle comme notice convertie, §7), numéros
NNT réels (bouchon), création de notices d'autorité, écriture dans le Sudoc,
Alma ou Orion, annotation et export de jeux de données (restent dans le POC).

## 2. Arborescence

```
humatheque-catalogage/
  CLAUDE.md  docs/SPEC.md  pyproject.toml  .env.example
  Dockerfile  docker-compose.yml
  spec/                     instantanés Grist (CSV), un fichier par table
  reference/                POC, maquette, mindmap, générateur de départ (lecture seule)
  src/humatheque/
    config.py               settings pydantic (env), chemins, seuils
    cli.py                  serve | check-spec | run-url | run-batch | scan-minio | spec-export
    ingestion/              lecture du bucket MinIO, constitution des documents (§3)
    texte_unimarc/          normalisation de chaînes, « module NOR » du wiki (§4)
      _vendor/reaccentue/   reaccentue vendorisé (+ dico/)
    extraction/             appel VLM multi-images, fournisseurs interchangeables
  config/prompts/           prompts d'extraction externalisés (VLM-T-005)
    contexte/               adaptateur extraction → Contexte + fonctions de calcul
    services/               clients HTTP : alma, sudoc, idref, dewey
    qualification/          agrégation des thesis_state Sudoc en état du document (pas d'appel réseau)
    spec/                   chargement Grist/CSV, modèles, vérificateur
    decision/               moteur Situations + Statuts_services, provenance
    conversion/             conversion d'une notice Sudoc existante (copie en v1)
    generateur/             moteur des Regles + sérialisation
    workflow/               orchestrateur : un document → un Dossier
    store/                  SQLite (lots, dossiers, versions de notices)
    application/            couche de services : cas d'usage, renvoie des modèles pydantic
    notice/                 notice éditable : analyse du texte WinIBW, validation, versions
    web/                    FastAPI : pages HTML (Jinja2 + HTMX) et API JSON /api/v1
      static/               CSS Tailwind compilé, htmx, Monaco, OpenSeadragon (vendorisés)
      tailwind/             sources Tailwind (config, input.css)
  tests/
```

## 3. Entrées

Formulaire unique (mode 1 et mode 2) :
- **Images** : téléversement d'un ou plusieurs fichiers, OU une URL, OU une
  liste d'URL (une par ligne, format de `minio_urls.txt` ; le suffixe ` FAIT`
  est ignoré), OU une sélection de dossiers du bucket MinIO (ING-F-005).
  Pour le téléversement, une option indique si les fichiers forment **un seul
  document multi-images** ou un document par fichier. Une URL isolée reste un
  document d'une image.
- **Type de document** : `these` | `memoire`. Pour un document issu du bucket,
  il est déterminé par le dossier parent `TH` ou `MM` (ING-F-009) et le champ
  du formulaire est ignoré.
- **Provenance** : n'est **pas saisie**. Elle est déduite de
  `granting_institution` pour les thèses (§6, étape 2).
- **Fournisseur VLM** : choix repris du POC, par défaut celui de `.env`.

Un envoi crée un **Lot** : un seul document donne un lot d'un document, en
mode 1. Les paramètres saisis s'appliquent à tous les documents du lot.

### 3.1 Ingestion depuis MinIO

- **Stockage** : S3 MinIO (`https://minio.smartbiblia.fr`, console
  `https://minio-console.smartbiblia.fr`), bucket et préfixe en configuration
  (`MINIO_*`).
- **Unité documentaire** (ING-F-001, ING-F-002) : un dossier = un document ;
  toutes les images du dossier lui appartiennent. Structure proposée :

  ```
  poc/
  ├── TH/
  │   ├── TH-01/
  │   │   ├── page_titre.jpg   # ou 001.jpg
  │   │   └── resume.jpg       # ou 002.png
  │   └── TH-02/
  └── MM/
      ├── MM-01/
      └── MM-02/
  ```

  Ordre des images : ordre lexicographique des noms de fichiers. L'identifiant
  du document est son chemin (`TH/TH-01`).
- **Document incomplet ou invalide** (ING-F-008) : dossier sans image, image
  illisible, ou extraction ne renvoyant aucune métadonnée du schéma (page ne
  correspondant à rien). Le document est signalé avec l'alerte
  `document_invalide` et le motif, routage `décision`.
- **Sélection manuelle** (ING-F-005) : la page `/` liste les dossiers du bucket
  (déjà traités ou non, avec leur dernier statut) et permet d'en lancer une
  sélection. `humatheque scan-minio` fait la même chose en ligne de commande.
- **Détection automatique et traitement planifié** (ING-F-003, ING-F-004 :
  **à arbitrer**). Prévoir l'architecture sans les activer par défaut :
  - balayage périodique du bucket (nouveaux dossiers = absents de l'historique)
    et route `POST /minio/evenements` pour les notifications d'évènements S3 ;
  - planification à heure(s) fixe(s) (`PLANIFICATION_CRON`, vide = désactivé),
    qui crée un lot avec les nouveaux documents ;
  - drapeaux `INGESTION_AUTO=false`, `PLANIFICATION_CRON=` dans `.env.example`.
- **Historique** (ING-F-006, ING-T-003, ING-T-004) : chaque balayage et chaque
  traitement est enregistré (date, déclencheur manuel / planifié /
  évènement, documents, résultat) et consultable dans l'interface (§9).

## 4. Package `texte_unimarc`

Fonctions pures, sans réseau, testées par tables de cas. Chaque fonction
renvoie `Resultat(valeur, alertes: list[str], trace: list[str])`.

### 4.1 Réaccentuation : `reaccentuer(texte) -> Resultat`

- **Base** : https://github.com/cquest/reaccentue, à vendoriser dans
  `_vendor/reaccentue/` (le code n'est pas un paquet pip).
  - Remplacer les chemins relatifs `dico/...` par des chemins relatifs au module.
  - Mettre le cache (`cache.pz`) dans `data/`.
  - Construire le cache au build Docker.
  - Le dépôt n'a pas de fichier LICENSE : le noter dans `_vendor/README.md`.
- **Ne pas appeler `reaccentue.reaccentue()`.** Elle remplace les mots par
  `str.replace` sur toute la chaîne, ce qui corrompt les sous-chaînes
  (« DEMOCRATIE » devient « deMOCRÀTIE »). Elle ne gère pas l'élision et
  produit une casse de titre (une majuscule à chaque mot, à la manière des noms
  de rues), pas une casse de phrase.
- **N'utiliser que son dictionnaire** `dico`, construit avec les mêmes règles :
  - clé = forme majuscule sans accent ;
  - valeur = liste des formes candidates ;
  - les clés « mot_précédent mot » donnent des fréquences de bigrammes.
- **Tokeniser soi-même** : mots, apostrophes d'élision (`L'`, `D'`, `QU'`…),
  traits d'union, ponctuation. Ensuite, pour chaque mot :
  - **un seul candidat** (une fois les candidats dédoublonnés en minuscules et
    restreints à l'alphabet latin) : on le prend ;
  - **plusieurs candidats** : on départage par la fréquence du bigramme avec le
    mot précédent ;
  - **égalité ou absence de données** : on garde la forme sans accent et on
    ajoute l'alerte `accent_ambigu:<MOT>`.
- **Noms propres** : construire un ensemble à partir des entrées en majuscule
  initiale du `.dic` hunspell, chargé **avant** la réduction du dictionnaire.
  - Un mot dont l'entrée n'existe qu'avec une majuscule (« Afrique ») garde sa
    majuscule.
  - Un mot dont l'entrée existe avec et sans majuscule (Rousseau / rousseau,
    Pascal / pascal) passe en minuscule avec l'alerte `nom_propre_possible`,
    **sauf** s'il figure parmi les noms propres connus du document
    (personnes et organismes extraits).
- **Ne s'applique qu'aux segments entièrement en capitales.** Un texte déjà en
  casse mixte n'est pas modifié.

Cas d'acceptation minimaux (vérifiés avec le dictionnaire de reaccentue) :

| Entrée | Sortie de `casse_phrase` |
|---|---|
| `CONTRIBUTION A L'ETUDE DE LA DEMOCRATIE EN AFRIQUE` | `Contribution à l'étude de la démocratie en Afrique` |
| `ECOLE PRATIQUE DES HAUTES ETUDES` | `École pratique des hautes études` |
| `ETUDE DE L'ETE 1968` | `Étude de l'été 1968` |
| `HISTOIRE DES ETATS-UNIS` | `Histoire des États-Unis` |
| `Les paysans de Normandie` | inchangé |

### 4.2 Casse

- **`casse_phrase(texte)`** : réaccentuation, puis minuscules sauf noms propres,
  puis majuscule sur le premier mot. S'applique aux zones 200$a, 328$c et 328$e.
- **`casse_complement_titre(texte)`** : comme `casse_phrase`, mais le premier
  mot reste en minuscule, sauf s'il s'agit d'un nom propre (règle ABES 200$e).
  Chaque sous-titre (répétable) est traité séparément.

### 4.3 Personnes : `personne(brut) -> Personne`

`Personne = {forme, nom, prenom, alertes}`. Le VLM rend les noms **tels
qu'imprimés** : casse d'origine, civilités comprises.

1. Retirer les civilités et titres : Monsieur, Madame, Mademoiselle, M., Mme,
   Mlle, Professeur, Pr, Pr., Dr, Docteur, Me…
2. Séparer le nom du prénom **avant toute conversion de casse** :
   - si une partie seulement des tokens est en capitales, ces tokens forment le nom ;
   - si tout est en capitales ou tout en casse mixte, le dernier token est le
     nom, en tenant compte des particules ; ajouter l'alerte `decoupage_heuristique`.
3. Particules `de`, `d'`, `du`, `des`, `de la`, `van`, `von`… : suivre la
   forme d'accès française (`$aLa Roche$bMarie-France de`). Ajouter l'alerte
   `particule_a_verifier` tant que les catalogueurs n'ont pas validé ce traitement.
4. Casse finale : chaque élément de nom ou de prénom commence par une majuscule,
   y compris après un trait d'union (`Jean-Daniel`). Pas de réaccentuation
   automatique des noms de personnes : un nom sans accent sur la page reste
   sans accent, et une alerte est ajoutée seulement s'il était en capitales.

| Entrée | `nom` | `prenom` | Alertes |
|---|---|---|---|
| `Jean-Daniel DUBOIS` | Dubois | Jean-Daniel | — |
| `M. Jean-Daniel DUBOIS` | Dubois | Jean-Daniel | — |
| `JEAN-DANIEL DUBOIS` | Dubois | Jean-Daniel | `decoupage_heuristique` |
| `Pr Marie-France de LA ROCHE` | La Roche | Marie-France de | `particule_a_verifier` |

### 4.4 Titre de classement : `titre_classement(titre, langue) -> Resultat`

- Insérer `@` devant le premier mot significatif lorsque le titre commence par
  un article. Le titre est déjà en casse de phrase à ce stade.
- Une table d'articles par langue est fournie en configuration
  (`fre, eng, ger, ita, spa` au départ).
- L'élision ne prend pas d'espace : `L'@évolution des pratiques`.
- Les autres articles sont suivis d'un espace : `Les @coraux tempérés`.
- Si aucun article ne précède : pas de `@`.
- Langue absente de la table : pas de `@`, et l'alerte `langue_sans_table_articles`.

## 5. Extraction et contexte

- **Extraction** : reprendre `providers.call_vlm` et `build_eval_prompt` du POC.
  - **Modèle par défaut** : `google/gemma-4-26B-A4B-it` (`.env`). Le modèle et
    le fournisseur se changent par configuration, sans toucher à la logique
    métier (VLM-T-006) : une interface `FournisseurVLM` unique, une classe par
    fournisseur OpenAI-compatible (reprises du POC).
  - **Multi-images** (VLM-T-001, ING-T-001) : toutes les images du document sont
    envoyées. La stratégie est un paramètre (`VLM_STRATEGIE`) :
    `requete_unique` (toutes les images dans une requête, valeur par défaut en
    attendant l'arbitrage VLM-T-009) ou `par_image` (une requête par image puis
    consolidation champ par champ, la première valeur non nulle dans l'ordre
    des images, les conflits étant signalés par l'alerte `extraction_divergente`).
  - **Prompt externalisé** (VLM-T-005) : `config/prompts/extraction_these.txt` et
    `extraction_memoire.txt`, versionnés ; le nom et l'empreinte du prompt sont
    enregistrés dans le dossier.
  - **`abstract`** (VLM-T-002, ING-T-002) : ajouté au schéma d'extraction
    (scalaire, null si absent), à `normalize_metadata` et au contexte.
  - **Séparation extraction / normalisation** (VLM-T-003) : le prompt ne fait
    que de l'extraction sémantique ; toute normalisation relève de
    `texte_unimarc` (module NOR).
  - **Conservation de la sortie brute** (VLM-T-004) : le JSON renvoyé par le
    modèle est stocké tel quel dans le dossier avant toute normalisation.

  Dans le prompt, **supprimer** :
  - la règle « all-caps → sentence case » ;
  - la règle de casse des noms de personnes ;
  - la règle de retrait des civilités.

  Ajouter à la place : « Transcribe every value exactly as printed (case,
  accents, civilities). » La normalisation passe par `normalize_metadata`
  (schéma v2).
- **Contexte** : `contexte.construire(extraction_v2, saisie, resultats_services)`
  produit un dict dont les clés sont les `id` de `spec/Sources.csv`.
  - **Champs simples** : le nom ne change pas ; on applique la casse du §4
    selon le champ (`title` → casse_phrase, `subtitle` → casse_complement_titre).
  - **Champs personnes** (`author`, `advisor`, `jury_president`, `reviewers`,
    `committee_members`) : listes de `{forme, nom, prenom, ppn, statut_idref}`.
    `author` et `jury_president` sont des listes de 0 ou 1 élément.
  - **Champs organismes** : `{forme, ppn, statut_idref}`.
  - **Valeurs calculées** :
    - `titre_classement` ;
    - `volumaison` : `volume` null donne « 1 volume » ; `volume` non null donne « ? volumes » ;
    - `code_etab` = constante `EPHE` (configuration) ;
    - `numero_nnt` = bouchon `XXXX` avec une alerte.
- **Cardinalité** : colonne `cardinalité` (`1` | `n`) de `Sources.csv` (présente
  dans l'export Grist ; accepter aussi l'en-tête sans accent). Le générateur
  répète une règle sur une source de cardinalité `n` (§8).
- **`volumaison`** est une source calculée : elle doit figurer dans `Sources`
  (type `calcul`), et R028 doit l'utiliser (`215 ##$a{volumaison}`). Le gabarit
  actuel `215 ##$a{volume} volume` est faux, car `volume` est le numéro du tome,
  pas le nombre de volumes. C'est à corriger dans Grist.

## 6. Workflow d'un document

Ordre (cf. mindmap) ; chaque étape écrit `{statut, requete, reponse, duree, erreur}`
dans le dossier :

1. **Extraction VLM**, puis construction du contexte partiel. JSON invalide :
   statut `incident` et arrêt.
2. **Provenance** (thèses uniquement ; un mémoire a la provenance `mémoire`).
   Fonction pure `provenance(granting_institution, co_tutelle_institutions)`,
   règles dans `config/provenance.yaml`, relisibles par les catalogueurs :
   - `EPHE` : `granting_institution` correspond à une forme connue de l'EPHE
     (« École pratique des hautes études », « EPHE », « E.P.H.E. », formes
     anciennes avec section…), comparée après normalisation (casse, accents,
     ponctuation) ;
   - `hors EPHE` : établissement présent et ne correspondant à aucune forme EPHE ;
   - `absent` : `granting_institution` vide ;
   - `ambigu` : correspondance partielle (par exemple « hautes études » sans
     « pratique »), ou EPHE présente seulement dans `co_tutelle_institutions`.

   `absent` et `ambigu` arrêtent le document (routage `décision`, motif
   « provenance à confirmer ») ; le catalogueur choisit EPHE ou hors EPHE et le
   pipeline reprend à l'étape 3. La règle appliquée et la forme comparée sont
   journalisées. **Modèle de décision** (optionnel, désactivé par défaut,
   `PROVENANCE_CONFIRMATION_LLM=false`) : un appel LLM texte peut confirmer la
   déduction ; en cas de désaccord avec la règle, le document part en décision.
3. **alma-checker** (`https://alma-checker.smartbiblia.fr`, route
   `/check/thesis` ou `/check/dissertation`). **Au moins une notice trouvée
   arrête le pipeline** :

   | Statut alma-checker | État Alma | Motif affiché |
   |---|---|---|
   | `academic_record_found` | `présent` | notice de thèse ou mémoire trouvée |
   | `ambiguous_academic_candidate` | `présent` | candidat incertain |
   | `off_profile_match_only` | `présent` | notice proche, pas de type TU |
   | `no_academic_record_found` | `absent` | — |

   Le PPN de `decision_support` est toujours repris dans le rapport. Un faux
   positif se traite par la reprise manuelle (ALM-SUD-006).
4. **sudoc-checker** (`/check/thesis` ou `/check/dissertation`, payload repris de
   `services.sudoc_request_payload`). La qualification originale / reproduction /
   microfiche est faite **par le service** : chaque candidat porte un
   `thesis_state` (`printed_original`, `printed_reproduction`,
   `microfiche_reproduction`, `electronic`, `unknown`). Le démonstrateur ne
   relit aucune notice Sudoc ; il **agrège** les `thesis_state` en un état du
   document (`qualification/`, fonction pure) :

   - candidats retenus : ceux de `candidates` dont `score.final` atteint
     `SUDOC_SEUIL_CORRESPONDANCE` (`.env`, aligné sur le seuil du service) ;
   - correspondance `thesis_state` → `etat_sudoc` dans `config/etats_sudoc.yaml` :

     | `thesis_state` | `etat_sudoc` |
     |---|---|
     | `printed_original` | `originale imprimée` |
     | `printed_reproduction` | `reproduction imprimée` |
     | `microfiche_reproduction` | `reproduction microfiche` |
     | `electronic` | `électronique seule` |

   - état du document = le plus fort parmi les candidats retenus, dans l'ordre
     `originale imprimée > reproduction imprimée > reproduction microfiche >
     électronique seule` ;
   - un candidat retenu `unknown` donne `indéterminé` (routage `décision`,
     motif « nature de la notice Sudoc non qualifiable ») : cette notice
     pourrait être l'originale, on ne tranche pas à sa place. **Exception** :
     si une originale imprimée est déjà retenue, l'`unknown` ne change rien et
     l'état reste `originale imprimée` ;
   - aucun candidat retenu : `ambigu` si le statut du service est
     `ambiguous_print_candidate`, sinon `rien` ;
   - statut `error`, ou `thesis_state` absent de la réponse (version du service
     antérieure) : routage `incident`, sans repli.

   Le dossier affiche le tableau de tous les candidats (PPN, score, `carrier`,
   `thesis_state`, retenu oui/non) et l'état agrégé avec sa justification.
5. **Situation** (§7). Action `Créer` : on continue. Action commençant par
   « Dériver » : conversion (§7) puis arrêt. Autre action : arrêt avec le
   rapport.
6. **idref-linker personnes** (`/align/person`, paramètres du POC) pour chaque
   personne, puis **idref-linker organisations** (`/align/thesis-organizations` ;
   lire l'OpenAPI) pour l'établissement de soutenance, l'école doctorale et les
   cotutelles.
7. **dewey-classifier** (`/classify`, payload repris du POC). La première classe
   donne `dewey_code`.
8. **Générateur** (§8), puis rapport.

Le dossier mémorise les résultats de chaque étape. **Relancer un dossier**
repart de la première étape en échec ou non faite ; les étapes réussies ne sont
pas rappelées, sauf demande explicite (ING-F-007).

**Vérification Alma avant Sudoc** (ALM-SUD-F-001) : l'ordre des étapes 3 et 4
est imposé ; la route appelée dépend du type de document (ING-T-005).

**Reprise après vérification manuelle** (ALM-SUD-006) : quand le traitement
s'arrête sur une vérification Alma ou Sudoc (situation de type sortie ou
décision), le catalogueur peut, après contrôle, déclarer le résultat non
pertinent (faux positif) avec un commentaire. Le pipeline reprend alors à
l'étape suivant le point d'arrêt, avec l'état corrigé (`absent` pour Alma,
`rien` pour Sudoc) ; la correction est journalisée.

**Gestion des erreurs** : toutes donnent un routage `incident` avec un motif
explicite et l'arrêt du document.

| Situation | Statut de l'étape | Reprise |
|---|---|---|
| Image inaccessible | `image_inaccessible` | relance automatique, puis manuelle |
| Service d'inférence indisponible | `inference_indisponible` | relance automatique, puis manuelle |
| Délai d'inférence dépassé (`VLM_TIMEOUT`) | `inference_timeout` | relance automatique, puis manuelle |
| API Alma, Sudoc, IdRef ou Dewey indisponible | `service_indisponible` | relance automatique, puis manuelle |
| Échec du générateur UNIMARC | `generation_echec` | manuelle |
| Échec de conversion (notice source illisible ou introuvable) | `conversion_echec` | manuelle |

Relance automatique : `RELANCES_AUTO` tentatives (défaut 2) avec délai
croissant ; au-delà, le dossier reste en incident et se relance depuis
l'interface. Tous les appels HTTP ont un délai maximal configurable (pas de
`timeout=None` comme dans le POC).

## 7. Décision

- **Clé de situation** : `(etat_alma, etat_sudoc, provenance)`, avec
  `provenance` = la valeur déduite à l'étape 2 du §6 pour une thèse (ou choisie
  par le catalogueur), `mémoire` pour un mémoire.
- **Moteur** : `Situations.csv`, première ligne qui correspond. `*` est un joker.
- Une cellule de `Situations` peut lister des alternatives séparées par ` | `
  (S07 : `reproduction imprimée | reproduction microfiche`).
- Valeurs admises pour `etat_sudoc` : celles que produit l'agrégation du §6
  (`originale imprimée`, `reproduction imprimée`, `reproduction microfiche`,
  `électronique seule`, `ambigu`, `rien`, `indéterminé`), plus `*`. Le
  vérificateur signale toute autre valeur. Les suffixes descriptifs comme
  `originale imprimée seule` (S02) sont acceptés par une table d'alias en
  configuration.
- **Aucune ligne ne correspond** : routage `décision`, motif
  « situation non prévue », clé affichée.
- **Action commençant par « Dériver » : conversion, puis arrêt** (thèses et
  mémoires), routage `conversion` quel que soit le routage de la table :
  - **notice source** : parmi les candidats Sudoc retenus dont l'état a
    déterminé `etat_sudoc`, celui qui a le meilleur score ; sa notice UNIMARC est
    lue via `https://www.sudoc.fr/{ppn}.xml` et convertie au format WinIBW ;
  - **conversion en v1** : les règles ne sont pas encore définies. La notice
    convertie est **une copie de la notice source**, avec l'alerte
    `conversion_non_implementee` et le profil cible affiché. Le module
    `conversion/` expose une fonction `convertir(notice_source, profil_cible)`
    qui sera remplacée sans toucher au workflow ;
  - **conservation** (ALM-SUD-F-002) : notice source et notice convertie sont
    stockées dans le dossier et téléchargeables ;
  - le rapport indique le PPN source, le profil cible et le lien vers le Sudoc.
- Une action `Arrêt` (S01, S04, S10) arrête sans conversion.
- **Statuts de service** : `Statuts_services.csv` associe `(service, statut)` à
  un routage `auto | décision | sortie | incident`. **Pour alma-checker et
  sudoc-checker**, le routage ne vient pas de cette table mais des
  correspondances du §6 (états) et de `Situations`. Seul leur statut `error`
  y est lu (`incident`). Les lignes actuelles de ces deux services dans Grist
  (`present_avec_ppn`, `reproduction_found`…) ne correspondent pas aux statuts
  réels des API : elles sont à remplacer par les statuts du §6. Pour IdRef et Dewey, un
  statut `décision` **n'arrête pas** la génération : on utilise le repli
  (`$a{nom}$b{prenom}` ou zone omise) et une exception est créée. `not_found`
  côté IdRef ajoute l'alerte « proto-notice d'autorité à créer ».
- **Routage final du dossier** : le plus sévère de ses étapes, dans l'ordre
  `incident > sortie > conversion > décision > auto`.

## 8. Spécification et générateur

**Chargement** : source choisie par `SPEC_SOURCE` (`csv` par défaut, ou
`grist`). En mode `csv`, `spec/*.csv`. En mode `grist` (`GRIST_URL`,
`GRIST_DOC_ID`, `GRIST_API_KEY`), l'API Grist ; si Grist est injoignable, on
retombe sur `spec/*.csv` avec un avertissement visible dans l'interface. Avec l'API Grist, les références numériques et les
listes `["L", …]` sont résolues en codes (cf. `reference/generateur_unimarc.py`).
Le bouton « Recharger la spec » de l'interface relit la source ; la page `/`
affiche la source active (CSV ou Grist) et l'état de Grist (joignable ou non).
**`humatheque spec-export`** écrit un instantané des tables Grist dans `spec/`,
au même format que les exports manuels, plus une copie horodatée dans
`spec/archives/` : c'est le moyen de figer une version de la spec. La version
chargée (horodatage + empreinte) est enregistrée dans chaque dossier.

**Tables attendues dans `spec/`** : `Regles`, `Sources`, `Situations`,
`Statuts_services` et `Profils`. Les deux dernières sont à exporter depuis
Grist. Si elles manquent, le chargement échoue avec un message explicite.

**Syntaxe stricte de `gabarit` et `repli`** (pas de nouvelle colonne dans `Regles`) :
- `ZZZ II$x…` : ouvre une occurrence de la zone ZZZ, indicateurs II (`#` =
  blanc). Une zone sans indicateurs (`008 $aAax3`) est une zone de contrôle ;
- `$x…` : fragment ajouté à la zone de la colonne `zone` (les 3 premiers
  caractères). Il rejoint l'occurrence ouverte par une règle précédente du même
  champ et du même profil, ou en ouvre une, avec les indicateurs `##` si aucune
  règle du champ n'en déclare ;
- `{var}` : variable du contexte. Pour une source de type personne ou
  organisme (cardinalité 1 ou n), les variables de l'élément (`{ppn}`,
  `{nom}`, `{prenom}`, `{forme}`) sont disponibles ; elles sont exemptées du
  contrôle « variable absente de `Sources` » ;
- l'ordre des sous-zones suit l'ordre des lignes de `Regles` ;
- interdits : `|`, `…`, `?`, `{?}`, sous-zone sans valeur (`105 ##$a`).

**Rendu** :
- On garde les règles du profil cible dont le statut n'est pas `rejeté` (filtre
  configurable). La notice affiche, pour chaque zone, l'identifiant et le statut
  de la règle.
- Une règle dont une source est de cardinalité `n` produit une occurrence par
  élément ; la colonne `condition` sert d'étiquette d'occurrence (701 directeur,
  701 rapporteur…).
- Si une variable manque, on essaie le `repli`. Sinon le fragment est omis. Une
  règle de priorité 1 omise produit une alerte.
- `mode_production` = `Non généré` : règle ignorée, et listée dans le rapport
  comme « non couverte ».

**Vérificateur** (`humatheque check-spec`, exécuté au démarrage et affiché
dans l'interface). Il signale :
- les gabarits non analysables ;
- les variables absentes de `Sources` ;
- les sources de cardinalité inconnue ;
- les profils inconnus ;
- les valeurs de `etat_sudoc` non admises dans `Situations` ;
- les lignes alma-checker et sudoc-checker de `Statuts_services` autres que
  `error` (avertissement : elles sont ignorées).

Les défauts relevés dans les premiers exports (R009 sous-zone vide, R032 `…`,
R028 sur `volume`) sont corrigés dans Grist : ils servent de cas de test du
vérificateur sur des CSV fabriqués.

**Sorties** : texte au format WinIBW, une zone par ligne, triée par étiquette
(format de référence) ; MARCXML via `pymarc`. Les deux sont téléchargeables.
L'export porte sur la **dernière version validée** de la notice (§9.2), à
défaut sur la version générée.

## 9. Interface web

### 9.1 Principes techniques

- **Rendu serveur + HTMX** : pages Jinja2, interactions par échange de
  fragments HTML (filtres, reprise d'une exception, suivi d'un lot par
  `hx-trigger="every 3s"` tant qu'il est en cours, compteurs mis à jour en
  `hx-swap-oob`). Pas de framework front, pas de build JavaScript.
- **Îlots JavaScript** là où HTMX ne suffit pas, et seulement là : l'éditeur
  de notice (Monaco) et la visionneuse d'images (OpenSeadragon). Conventions
  pour éviter les pièges connus d'HTMX :
  - chaque îlot est un composant autonome (`static/js/<nom>.js`) initialisé
    sur `htmx:load` et détruit proprement avant remplacement
    (`htmx:beforeSwap`) ; un îlot n'est jamais à l'intérieur d'un fragment
    rafraîchi périodiquement (`hx-preserve` sinon) ;
  - les îlots échangent avec le serveur en JSON (`/api/v1`), pas en fragments ;
  - navigation : `hx-push-url` sur les changements de vue pour que l'historique
    du navigateur et les liens directs fonctionnent ;
  - erreurs : un gestionnaire global `htmx:responseError` affiche un bandeau,
    jamais d'échec silencieux.
- **Couche de services** (`application/`) : chaque écran et chaque action
  passe par une fonction qui renvoie des modèles pydantic. Les routes HTML et
  l'API JSON `/api/v1` appellent les mêmes fonctions. Aucune logique métier
  dans les routes ni dans les gabarits. Un front différent (React par exemple)
  pourra se brancher sur l'API sans réécrire la logique.
- **Style** : Tailwind CSS compilé par l'**exécutable autonome** (sans Node),
  jetons de couleur repris de la maquette (palette, badges de routage), une
  seule feuille compilée dans `static/`. Soigner les états vides, de
  chargement et d'erreur ; badges de routage identiques sur tous les écrans.
- **Visionneuse** : OpenSeadragon (zoom, déplacement, défilement entre les
  images du document), vendorisé dans `static/`.

### 9.2 Édition de la notice

- **Éditeur** : Monaco (distribution `min` vendorisée, chargée par son
  chargeur AMD, sans build), au format WinIBW (une zone par ligne,
  `ZZZ II$a…`), avec un langage Monarch pour la coloration (étiquette,
  indicateurs, codes de sous-zones).
- **Validation** côté serveur (`notice/`), appelée par l'éditeur à la pause de
  frappe et à l'enregistrement (`POST /api/v1/notices/{id}/validation`) ; les
  résultats s'affichent comme marqueurs Monaco (ligne, gravité, message) :
  - syntaxe : étiquette sur 3 chiffres, indicateurs, sous-zones `$x` non vides ;
  - règles de la spec : zones de priorité 1 du profil absentes, zones ou
    sous-zones produites par aucune règle du profil (avertissement), PPN mal
    formés.
- **Versions** : la notice générée (ou convertie) est conservée telle quelle ;
  chaque enregistrement crée une version (auteur, date, différence avec la
  précédente). Statuts : `générée` → `éditée` → `validée` (action explicite
  du catalogueur, refusée s'il reste des erreurs).
- **Régénération après édition** : si une résolution d'exception (PPN, classe
  Dewey) régénère la notice alors qu'une version éditée existe, la nouvelle
  version générée **n'écrase pas** l'édition : l'interface affiche les
  différences et le catalogueur choisit de les reporter ou non.
- **API** : `GET/PUT /api/v1/notices/{id}` (texte + version),
  `GET /api/v1/notices/{id}/versions`, `POST …/validation`,
  `POST …/valider` ; mêmes fonctions de `application/` que les pages.

### 9.3 Écrans

- **`/` Nouveau traitement** : le formulaire du §3, la liste des dossiers du
  bucket MinIO avec leur dernier statut et sélection multiple (ING-F-005),
  l'état de la spec chargée et les avertissements du vérificateur. Si le
  fournisseur VLM est un endpoint Hugging Face dédié : indicateur d'état et
  bouton de réveil de l'endpoint (repris du POC).
- **`/historique`** (ING-F-006, ING-T-004) : balayages et traitements, avec date,
  déclencheur, nombre de documents, routages, lien vers chaque lot.
- **`/lots/{id}` Lot** (mode 2) :
  - les indicateurs de la maquette : documents, passés seuls, en attente,
    repris ;
  - les filtres Décisions / Sorties / Conversions / Incidents ;
  - la file d'exceptions (document, étape, motif, bouton Reprendre) ;
  - le journal ;
  - l'export : CSV du rapport et fichier texte de toutes les notices produites.
- **`/dossiers/{id}` Dossier** (mode 1, et cible de « Reprendre »). Mise en
  page en vis-à-vis :
  - **panneau gauche** : les images du document, avec défilement d'une image à
    l'autre ;
  - **panneau droit, haut** : les métadonnées extraites ;
  - **panneau droit, bas** : les informations du traitement (date, modèle,
    statut), le motif de l'arrêt s'il y a lieu (ALM-SUD-F-005), les sorties
    détaillées de chaque brique, et le bouton « Relancer l'extraction » ;
  - pour le résultat final, la notice UNIMARC en vis-à-vis des images, dans
    l'éditeur (§9.2) ;
  - pour une conversion : images à gauche, notice Sudoc avant transformation en
    haut à droite, notice produite par la conversion en bas à droite, éditable.

  Contenu :
  - une frise des étapes avec statut et durée ;
  - l'extraction brute, puis normalisée, avec le diff des transformations du §4 ;
  - le contexte ;
  - la situation retenue et la ligne de `Situations` correspondante ;
  - les candidats Alma, Sudoc, IdRef et Dewey avec liens, et pour les notices
    trouvées leurs informations principales : PPN, titre, auteur, année, type
    (ALM-SUD-F-004) ;
  - les réponses brutes des API, repliées par défaut et dépliables
    (ALM-SUD-F-003) ;
  - la notice, chaque zone annotée par sa règle ;
  - les alertes.

  **Résolution d'exception** :
  - IdRef : choisir un PPN candidat ou saisir un PPN ;
  - Dewey : choisir une classe ;
  - après l'une ou l'autre, la notice est régénérée ;
  - situation : marquer « traité hors outil », avec un commentaire ;
  - vérification Alma ou Sudoc : déclarer un faux positif et reprendre le
    pipeline (§6, ALM-SUD-006) ;
  - provenance absente ou ambiguë : choisir EPHE ou hors EPHE et reprendre.
  - Chaque résolution est journalisée (qui / quand / quoi).

## 10. Persistance, configuration, déploiement

- **SQLite**. Tables `lots`, `dossiers` et `ingestions` (historique des
  balayages et déclenchements, ING-T-003) ; le dossier est un JSON versionné par
  un champ `schema_version`. Images téléversées dans `data/uploads/`.
- **Exécution** : un `ThreadPoolExecutor` borné (`MAX_WORKERS`, 2 par défaut),
  sans courtier de messages.
- **`.env.example`** :
  - les variables du `config.py` du POC (VLM, Albert, HF, sudoc, idref, dewey) ;
  - `ALMA_CHECKER_API_URL`, `ALMA_API_KEY` ;
  - `SPEC_SOURCE=csv`, `GRIST_URL=http://grist:8484`, `GRIST_DOC_ID`,
    `GRIST_API_KEY`, `GRIST_SESSION_SECRET`, `GRIST_DEFAULT_EMAIL` ;
  - `CODE_ETAB=EPHE`, `MAX_WORKERS`, `SUDOC_SEUIL_CORRESPONDANCE` ;
  - `VLM_MODEL=google/gemma-4-26B-A4B-it`, `VLM_STRATEGIE=requete_unique`,
    `VLM_TIMEOUT`, `HTTP_TIMEOUT`, `RELANCES_AUTO=2` ;
  - `PROVENANCE_CONFIRMATION_LLM=false` ;
  - `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_PREFIXE=poc/`, `MINIO_ACCESS_KEY`,
    `MINIO_SECRET_KEY`, `INGESTION_AUTO=false`, `PLANIFICATION_CRON=` ;
  - `REGLES_STATUTS_EXCLUS=rejeté`.
- **Déploiement** :
  - `Dockerfile` : image `python:3.12-slim` avec `uv` ; le cache reaccentue est
    construit au build ; l'exécutable autonome Tailwind est téléchargé (version
    figée) et compile la feuille de style au build. En développement :
    `uv run humatheque css --watch` ;
  - `docker-compose.yml` : **deux services**, même convention que les autres
    services smartbiblia :
    - `app` : construit depuis le `Dockerfile`, port 8000, volume `./data`,
      `env_file: .env`, `depends_on: grist` (sans en dépendre pour démarrer :
      l'app fonctionne avec `spec/*.csv` si Grist est absent) ;
    - `grist` : image officielle `gristlabs/grist`, port 8484, volume
      `./grist-data:/persist`, variables `GRIST_SESSION_SECRET`,
      `APP_HOME_URL`, `GRIST_DEFAULT_EMAIL`, `GRIST_SINGLE_ORG=humatheque` ;
    - réseau commun : l'app joint Grist par `http://grist:8484`
      (`GRIST_URL` dans `.env`) ;
    - `./data` et `./grist-data` dans `.gitignore`.
  - **Initialisation de Grist** (documentée dans le `README.md`, manuelle une
    fois) : importer le classeur de spécification dans l'instance locale,
    relever l'identifiant du document et créer une clé d'API, puis renseigner
    `GRIST_DOC_ID`, `GRIST_API_KEY` et `SPEC_SOURCE=grist` ;
  - en local : `uv run humatheque serve` seul (mode CSV), ou
    `docker compose up -d` pour l'app et Grist.

## 11. Tests et ordre de construction

Chaque étape se termine par `uv run pytest` vert. Services mockés avec `respx`
(réponses d'exemple tirées des README et OpenAPI).

1. **Socle** : `pyproject`, configuration, chargement de la spec et
   vérificateur. Test : `check-spec` sur les exports actuels de `spec/` ne
   signale aucune erreur bloquante (R009, R028 et R032 ont été corrigés dans
   Grist) ; tests unitaires du vérificateur sur des CSV fabriqués contenant
   chacun des défauts du §8. Chargement Grist testé avec des réponses d'API
   simulées (`respx`), y compris le repli sur CSV quand Grist est injoignable.
2. **`texte_unimarc`** : toutes les tables de cas du §4. **Provenance** : table
   de cas (« École pratique des hautes études », « EPHE », « E.P.H.E. Ve
   section » → EPHE ; « Université Paris 1 » → hors EPHE ; vide → absent ;
   « École des hautes études » → ambigu ; EPHE en cotutelle seulement → ambigu).
3. **Générateur** sur deux contextes écrits à la main (thèse originale EPHE,
   reproduction hors EPHE). Tests de référence : 105 en une seule occurrence
   (`$bm$ba` ou `$bv$ba`), 328 avec `$zReproduction de` en tête pour la
   reproduction, une 701 par rapporteur, repli `$a$b` sans PPN.
4. **Décision** : une ligne de test par situation S01 à S13, plus « situation
   non prévue » et « Dériver → conversion puis arrêt ».
5. **Clients des services, agrégation `thesis_state` et workflow** de bout en bout
   (mocks). Agrégation testée par table : originale + reproduction → originale ;
   reproduction + unknown → indéterminé ; originale + unknown → originale ;
   électronique seule ; aucun retenu + `ambiguous_print_candidate` → ambigu ;
   `thesis_state` absent → incident.
   Le scénario S08 produit une notice ; le S01 s'arrête avec le PPN Alma (y
   compris pour `off_profile_match_only`) ; le S06 produit une notice convertie
   identique à la source, avec l'alerte `conversion_non_implementee`, et
   s'arrête ; une provenance ambiguë arrête en décision puis reprend après
   choix manuel.
6. **Interface** : mode 2 (lot et file d'exceptions) en priorité, puis mode 1,
   puis résolution et historique. Tests de la couche `application/` (sans
   HTTP) et de quelques routes HTML et JSON.
6 ter. **Édition de notice** : analyse et validation du texte WinIBW (table de
   cas valides et invalides), versions, validation refusée en cas d'erreur,
   régénération sans écrasement d'une version éditée ; essai manuel de
   l'éditeur dans le navigateur.
6 bis. **Ingestion MinIO** : constitution des documents à partir d'un bucket
   simulé (dossiers TH/MM, multi-images, dossier vide → `document_invalide`),
   sélection manuelle, historique.
7. **Docker Compose** (app + Grist) : `docker compose up -d` démarre les deux
   services ; après import du classeur dans Grist, `check-spec` en mode `grist`
   donne le même résultat qu'en mode `csv` sur les mêmes exports, et
   `spec-export` reproduit les CSV. Puis essai réel sur 5 URL de
   `minio_urls.txt` et sur 5 dossiers du bucket `poc/`. Consigner les écarts dans `docs/ESSAI.md`.
8. **Après arbitrage seulement** : détection automatique et planification
   (ING-F-003, ING-F-004).

## 12. Traçabilité avec les exigences du wiki

| Exigence wiki | Section | État |
|---|---|---|
| ING-F-001, ING-F-002 | §3.1 | intégrée |
| ING-F-003, ING-F-004 | §3.1, §11 étape 8 | à arbitrer, architecture prévue, désactivée |
| ING-F-005, ING-F-006 | §3, §3.1, §9 | intégrée |
| ING-F-007 | §6 | déjà couverte |
| ING-F-008 | §3.1 | intégrée |
| ING-F-009 | §3 | intégrée (documents du bucket) |
| ING-T-001, VLM-T-001 | §5 | intégrée |
| ING-T-002, VLM-T-002 | §5 | intégrée (la zone 330 reste `Non généré` dans Grist) |
| ING-T-003, ING-T-004 | §3.1, §9, §10 | intégrée |
| ING-T-005 | §6 | déjà couverte |
| VLM-T-003, VLM-T-004 | §5 | intégrée |
| VLM-T-005, VLM-T-006 | §5 | intégrée |
| VLM-T-007 | — | étude, hors v1 |
| VLM-T-008 | §5, §10 | question ouverte : fournisseur configurable |
| VLM-T-009 | §5 | question ouverte : stratégie configurable, défaut `requete_unique` |
| ALM-SUD-F-001 | §6 | intégrée |
| ALM-SUD-F-003, F-004, F-005 | §9 | intégrée |
| ALM-SUD-006 | §6, §9 | intégrée |
| ALM-SUD-F-002, conversions Sudoc | §7, §9 | intégrée (conversion = copie de la source en v1 ; décision du 03/11) |
| Alma : au moins une notice trouvée → arrêt | §6 | intégrée (décision du 03/11) |
| Provenance déduite de `granting_institution` | §6, §7 | intégrée, modèle de décision optionnel (décision du 03/11) |
| Conversion « reproduction via 105 $bm, EPHE » | — | coquille du wiki : `Situations` S07 fait foi |
| Mémoire trouvé au Sudoc | §7 | la spec fait foi : conversion si notice électronique (S13) |
