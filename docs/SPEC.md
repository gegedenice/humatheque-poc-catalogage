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
  regles/                   règles de catalogage (§7, §8) : variables, pipeline, notice/ (socle, variantes)
  reference/                POC, maquette, mindmap, moteur-regles/ (lecture seule)
  src/humatheque/
    config.py               settings pydantic (env), chemins, seuils
    cli.py                  serve | check-spec | run-url | run-batch | scan-minio | css
    ingestion/              lecture du bucket MinIO, constitution des documents (§3)
    texte_unimarc/          titre de classement, sous-titre, nom / prénom (§4)
    extraction/             appel VLM multi-images, fournisseurs interchangeables
  config/prompts/           prompts d'extraction externalisés (VLM-T-005)
    contexte/               adaptateur extraction → Contexte + fonctions de calcul
    services/               clients HTTP : alma, sudoc, idref, dewey
    qualification/          agrégation des thesis_state Sudoc en état du document (pas d'appel réseau)
    regles/                 chargement des règles YAML, moteur, vérificateur
    decision/               provenance, valeurs lues par le pipeline, décision
    conversion/             conversion d'une notice Sudoc existante (copie en v1)
    generateur/             remplissage des profils + sérialisation
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
- **Paramètres avancés** (décision du 04/11), repliés par défaut : quelques
  paramètres des services modifiables pour le lot (seuils d'acceptation et
  marge IdRef des personnes, seuil d'acceptation IdRef des organismes,
  méthode Dewey). La liste, les bornes et les valeurs par défaut sont dans
  `config/services.yaml` (`reglables`) ; les valeurs retenues sont
  enregistrées dans le lot et visibles dans son bandeau.

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
  illisible, extraction ne renvoyant aucune métadonnée du schéma (page ne
  correspondant à rien) ou sans titre (décision du 05/11 : les vérifications
  Alma et Sudoc exigent un titre). Le document est signalé avec l'alerte
  `document_invalide` et le motif, issue « à reprendre ».
- **Sélection manuelle** (ING-F-005) : la page `/` liste les dossiers du bucket
  (déjà traités ou non, avec leur dernier statut) et permet d'en lancer une
  sélection. `humatheque scan-minio` fait la même chose en ligne de commande.
- **Détection automatique et traitement planifié** (ING-F-003, ING-F-004 ;
  décisions du 05/11). Trois déclencheurs au choix, réglés dans la page
  d'administration (§9.3), désactivés par défaut :
  - balayage périodique du bucket toutes les N minutes (nouveaux dossiers =
    absents de l'historique) ;
  - traitement à heure fixe (expression cron) ;
  - évènements S3 : `POST /minio/evenements?jeton=…` (jeton affiché dans
    l'administration) ; le lot est créé après un délai sans nouvel évènement
    (stabilisation : un dossier se dépose objet par objet).
  Un planificateur interne à l'application (fil d'exécution, une seule
  instance) applique ces réglages. Un seul lot automatique à la fois : les
  dossiers arrivés pendant un lot attendent le déclenchement suivant. Les lots
  automatiques utilisent le fournisseur et les paramètres avancés de
  l'administration. Un dossier déjà traité dont les images changent est
  signalé (« modifié »), jamais retraité d'office. L'accueil affiche les lots
  automatiques créés depuis la dernière visite (pas de courriel).
  Valeurs initiales dans `.env` : `INGESTION_AUTO`, `BALAYAGE_INTERVALLE`,
  `PLANIFICATION_CRON`, `EVENEMENTS_DELAI`.
- **Historique** (ING-F-006, ING-T-003, ING-T-004) : chaque balayage et chaque
  traitement est enregistré (date, déclencheur manuel / planifié /
  évènement, documents, résultat) et consultable dans l'interface (§9).

## 4. Package `texte_unimarc`

Décision du 11/11 : la réaccentuation par dictionnaire (reaccentue) et la mise
en casse en Python étaient trop fragiles (« UNIVERSITE PARIS IX DAUPHINE »
devenait « Université Paris ix Dauphiné »). La **normalisation lexicale est
confiée au VLM** par le prompt (§5) : capitales → casse de phrase avec accents,
noms de personnes en casse de phrase, civilités retirées. Le dictionnaire
(25 Mo) et la commande `cache-reaccentue` sont retirés.

Restent en Python, fonctions pures, listes dans `config/texte_unimarc.yaml` :

- **`titre_classement(titre, langue)`** : `@` devant le premier mot significatif
  (article initial selon la langue ; élision sans espace : `L'@évolution`) ;
- **`minuscule_initiale(sous_titre)`** : première lettre du sous-titre en
  minuscule (200 $e), sauf sigle ou nombre romain en capitales ;
- **`personne(brut)`** : découpage nom / prénom pour les zones d'autorité
  ($a nom, $b prénom), sans changer l'écriture des mots : un bloc en capitales
  est le nom, sinon le dernier mot ; particules (`de`, `d'`, `von` rejetées
  après le prénom, `La`, `Du`… gardées en tête du nom) ; civilités résiduelles
  retirées.

## 5. Extraction et contexte

- **Extraction** : reprendre `providers.call_vlm` et `build_eval_prompt` du POC.
  - **Modèle par défaut** : `google/gemma-4-26B-A4B-it` (`.env`). Le modèle et
    le fournisseur se changent par configuration, sans toucher à la logique
    métier (VLM-T-006) : une interface `FournisseurVLM` unique, une classe par
    fournisseur OpenAI-compatible (reprises du POC).
  - **Fournisseurs** (décision du 11/11) : modèles ouverts uniquement —
    `albert` (**par défaut**, modèle `ALBERT_MODEL` de `.env`), `vlm` (point
    d'accès on-premise, ex. llama.cpp) et `hf`. OpenAI est retiré ; un réglage
    d'administration qui le désignait revient au fournisseur par défaut.
  - **Multi-images** (VLM-T-001, ING-T-001) : toutes les images du document sont
    envoyées. La stratégie est un paramètre (`VLM_STRATEGIE`) :
    `requete_unique` (toutes les images dans une requête, valeur par défaut en
    attendant l'arbitrage VLM-T-009) ou `par_image` (une requête par image puis
    consolidation champ par champ, la première valeur non nulle dans l'ordre
    des images, les conflits étant signalés par l'alerte `extraction_divergente`).
  - **Prompt externalisé** (VLM-T-005) : `config/prompts/extraction_these.txt` et
    `extraction_memoire.txt`, versionnés et **modifiables dans l'interface**
    (Administration › Prompts : vérification du schéma JSON, archive, journal) ;
    le nom et l'empreinte du prompt sont enregistrés dans le dossier.
  - **Prompts de l'atelier** (décision du 11/11) : retour aux prompts initiaux,
    qui demandent au VLM la normalisation lexicale (« If source text is
    written in all-caps, transform it into sentence case with French
    accents », casse des noms de personnes, retrait des civilités). Le champ
    `abstract` n'est plus demandé (VLM-T-002 : à rajouter dans le prompt si
    besoin ; la variable `resume` est indisponible).
  - **Modèle** : chaque fournisseur a son modèle dans `.env` ; l'administration
    peut en choisir un autre parmi ceux que l'API du fournisseur déclare
    (`GET …/models`) ; le choix est enregistré dans les paramètres du lot.
  - **Conservation de la sortie brute** (VLM-T-004) : le JSON renvoyé par le
    modèle est stocké tel quel dans le dossier avant toute normalisation.

- **Contexte** : `contexte.construire(extraction_v2, saisie, resultats_services)`
  produit les valeurs normalisées du document (clés du schéma d'extraction et
  valeurs calculées) ; les **variables** lues par le générateur portent les
  noms de `regles/variables.yaml` (`titre`, `auteur`, `directeur`, `annee`…).
  La colonne `source` de ce fichier indique d'où vient chaque valeur
  (`extraction.defense_year`, `calcul`, `constante`, `service.dewey-classifier`) :
  l'adaptateur s'en sert pour passer des noms du schéma d'extraction aux noms
  des profils (une source non `extraction.*` porte le nom de la variable).
  - **Champs simples** : repris tels que le VLM les écrit, sauf `subtitle`
    (minuscule initiale, §4). Le champ `transformation` de `variables.yaml`
    décrit, pour chaque variable, ce qui est fait entre le prompt et la notice ;
    la page Administration › Prompts l'affiche à côté du prompt (« Du prompt à
    la notice »).
  - **Personnes** (`type: personne`) : `{forme, nom, prenom, ppn, statut_idref}`,
    en liste si `liste: true` (`directeur`, `rapporteur`, `membre_jury`), seul
    sinon (`auteur`, `president`). `ppn` n'est renseigné que si l'alignement est
    `accepted` ou validé par un catalogueur.
  - **Organismes** (`type: organisme`) : `{forme, ppn, statut_idref}`.
  - **Valeurs calculées** :
    - `titre_classement` ;
    - `volumaison` : `volume` null donne « 1 volume » ; `volume` non null donne « ? volumes » ;
    - `code_etab` = constante `EPHE` (configuration `CODE_ETAB`).
- **Variables indisponibles** (`disponible: false` dans `variables.yaml`, par
  exemple `numero_nnt`) : le contexte ne les fournit pas ; les sous-zones qui
  les utilisent sont omises.

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

   La provenance donne la variable `ephe` du pipeline (§7) : `oui` (EPHE),
   `non` (hors EPHE), `absent`, `ambigu`. Pour une thèse, `absent` et
   `ambigu` → STOP – à reprendre (décision du 09/11). Depuis le 11/11, la
   provenance est calculée **après Alma** (ordre de `regles/pipeline.yaml`) :
   Alma n'utilise pas `ephe` ; l'arrêt intervient donc avant Sudoc ; le catalogueur choisit EPHE ou
   hors EPHE dans la carte « Bilan » du dossier et le traitement reprend. La règle appliquée et la forme comparée sont
   journalisées. **Modèle de décision** (optionnel, désactivé par défaut,
   `PROVENANCE_CONFIRMATION_LLM=false`) : un appel LLM texte peut confirmer la
   déduction ; en cas de désaccord avec la règle, le document part en décision.
3. **alma-checker** (`https://alma-checker.smartbiblia.fr`, route
   `/check/thesis` ou `/check/dissertation`). **Au moins une notice trouvée
   arrête le pipeline.** Décision du 05/11 : une notice au-dessus de
   `match_threshold` arrête **sans alerte** (« Notice déjà dans Alma », issue « terminé », affiché en
   vert) ; seul un candidat incertain (entre `ambiguous_threshold` et
   `match_threshold`, ou hors type TU) est à reprendre (« Candidat Alma à vérifier ») :

   | Statut alma-checker | État Alma | Motif affiché |
   |---|---|---|
   | `academic_record_found` | `présent` | notice de thèse ou mémoire trouvée |
   | `ambiguous_academic_candidate` | `incertain` | candidat incertain |
   | `off_profile_match_only` | `incertain` | notice proche, pas de type TU |
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
     `duplicate_threshold` (`config/services.yaml`, décision du 05/11),
     envoyé au service : le statut du service et l'agrégation utilisent le
     même seuil ;
   - codes gardés tels quels (décision du 09/11, plus de traduction) ;
     `thesis_state` du document = le plus fort parmi les candidats retenus,
     dans l'ordre `printed_original > printed_reproduction >
     microfiche_reproduction > electronic` (règle technique, dans le code) ;
   - un candidat retenu `unknown` donne `thesis_state = unknown` : cette
     notice pourrait être l'originale, on ne tranche pas à sa place.
     **Exception** : si une originale imprimée est déjà retenue, l'`unknown`
     ne change rien ;
   - aucun candidat retenu : pas de `thesis_state` ; le `status` du service décide (§7) ;
   - erreur du service (réponse HTTP 4xx/5xx ou service injoignable ; le
     service n'a pas de statut `error`), ou `thesis_state` absent de la
     réponse (version du service antérieure) : issue « incident », sans repli.

   Le dossier affiche le tableau de tous les candidats (PPN, score, `carrier`,
   `thesis_state`, retenu oui/non) et l'état agrégé avec sa justification.
5. **Décision** (§7). Action `création` : on continue. Action `conversion` :
   conversion (§7) puis arrêt. Action `arrêt` ou `décision` : arrêt avec le
   rapport.
6. **idref-linker personnes** (`/align/person`, paramètres du POC) pour chaque
   personne, puis **idref-linker organisations** (`/align/thesis-organizations` ;
   lire l'OpenAPI) pour l'établissement de soutenance, l'école doctorale et les
   cotutelles.
7. **dewey-classifier** (`/classify`, payload repris du POC, méthode `albert`
   par défaut, décision du 04/11). La première classe donne `dewey_code`.

**Paramètres de requête des services** (décision du 04/11) : les paramètres
qu'une requête peut porter (seuils, nombres de candidats, poids IdRef,
méthode Dewey…) sont envoyés explicitement à chaque appel, avec les valeurs
de l'environnement des services reprises dans `config/services.yaml` ; ils
sont donc journalisés avec la requête. Les paramètres internes aux services
(points d'accès SRU et Qualinka, modèles, clés Albert) restent dans leur
propre environnement.
8. **Générateur** (§8), puis rapport.

Le dossier mémorise les résultats de chaque étape. Après une reprise, le
traitement repart de l'étape concernée sans rappeler les étapes réussies
(ING-F-007). Le bouton **« Relancer »** du dossier (décision du 09/11 : un
seul bouton) relance **tout le pipeline**, extraction comprise ; les choix de
reprise déjà faits (provenance, faux positif, PPN) sont conservés.

**Vérification Alma avant Sudoc** (ALM-SUD-F-001) : l'ordre des étapes 3 et 4
est imposé ; la route appelée dépend du type de document (ING-T-005).

**Reprise après vérification manuelle** (ALM-SUD-006) : quand le traitement
s'arrête sur une vérification Alma ou Sudoc (document à reprendre ou
décision), le catalogueur peut, après contrôle, déclarer le résultat non
pertinent (faux positif) avec un commentaire. Le pipeline reprend alors à
l'étape suivant le point d'arrêt, avec l'état corrigé (`absent` pour Alma,
`rien` pour Sudoc) ; la correction est journalisée.

**Gestion des erreurs** : toutes donnent l'issue « incident » avec un motif
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

## 7. Pipeline et décisions

Décision du 09/11 : **simple**. Le pipeline est décrit dans
`regles/pipeline.yaml` (studio › Pipeline) : les étapes dans l'ordre, et pour
chacune un **arbre de décision** sur les **codes renvoyés par l'API, sans
traduction** (`academic_record_found`, `duplicate_found`, `printed_original`,
`accepted`…). Plus de dictionnaire de faits, de règles nommées, d'états de
vérification ni de distinction action / issue.

- **Variables** : à toutes les étapes, `type` (`these`, `memoire` : dossier
  S3 TH / MM ou choix à l'envoi) et `ephe` (`oui`, `non`, `absent`, `ambigu`,
  d'après `granting_institution`, §6) ; puis celles de l'étape : `status`
  (Alma, Sudoc, IdRef, Dewey) et `thesis_state` (Sudoc). Les valeurs possibles
  sont déclarées par étape, d'après l'OpenAPI des services.
- **Arbre** : un nœud a des conditions `si` (ET entre variables, OU entre les
  valeurs d'une liste) et `sauf` (valeurs exclues), puis des sous-branches
  ou un résultat. Lecture de haut en bas : la première feuille qui correspond
  l'emporte ; une branche dont aucune sous-branche ne correspond laisse la
  lecture continuer ; à la fin, le `sinon` de l'étape.
- **Résultats** (une seule notion) :

  | Résultat | Effet | Issue |
  |---|---|---|
  | `stop` | arrêt, rien à faire | terminé |
  | `stop à reprendre` | arrêt pour vérification manuelle | à reprendre |
  | `conversion` (notice) | conversion de la notice Sudoc puis arrêt | terminé |
  | `poursuite` | étape suivante | — |
  | `poursuite avec alerte` | étape suivante, alerte au dossier | — |
  | `poursuite à reprendre` | étape suivante, exception : document à reprendre à la fin | à reprendre |
  | `création` (notice) | création de la notice | terminé |

  Une panne technique (service injoignable, réponse illisible, JSON
  d'extraction invalide) donne **incident**, sans règle. L'issue du dossier
  est la plus sévère de ses étapes (`incident > à reprendre > terminé`).
- **Pipeline actuel** :

  1. **Provenance** : `ephe = absent OU ambigu` → stop à reprendre ; sinon poursuite.
  2. **Alma** (`status`) : `academic_record_found` → stop ;
     `ambiguous_academic_candidate OU off_profile_match_only` → stop à reprendre ;
     `no_academic_record_found` → poursuite ; sinon stop à reprendre.
  3. **Sudoc** (`status`, `thesis_state`) :
     - thèse, `duplicate_found` :
       `printed_original` ET `ephe = non` → conversion these-reproduction-imprimee ;
       `printed_reproduction` ET `ephe = oui` → conversion these-originale-imprimee ;
       `microfiche_reproduction` ET `ephe = non` → conversion these-reproduction-imprimee ;
       `microfiche_reproduction` ET `ephe = oui` (improbable) → conversion these-originale-imprimee ;
       `unknown` → stop à reprendre ;
     - thèse, `electronic_only` : `ephe = non` → conversion these-reproduction-imprimee ;
       `ephe = oui` → conversion these-originale-imprimee ;
     - mémoire : `duplicate_found` → stop ; `electronic_only` → conversion memoire-original-imprime ;
     - `ambiguous_print_candidate` → stop à reprendre ; `no_print_duplicate_found` → poursuite ;
     - sinon → stop à reprendre (non prévus : `printed_reproduction` hors EPHE ;
       `printed_original` à l'EPHE, impossible ; `electronic` avec `duplicate_found`).
  4. **IdRef** (`status`, pour chaque personne et organisme) : `accepted` →
     poursuite ($3 = PPN) ; `low_confidence OU ambiguous` → poursuite à reprendre
     ($a nom $b prénom en attendant ; candidats proposés) ; `not_found` →
     poursuite avec alerte « non trouvé dans IdRef » ($a $b).
  5. **Dewey** : aucune vérification → poursuite (`status` calculé :
     `au_dessus_seuil`, `sous_seuil`, `aucune_classe`).
  6. **Création de la notice** : thèse ET `ephe = oui` → these-originale-imprimee ;
     thèse ET `ephe = non` → these-reproduction-imprimee ; mémoire → memoire-original-imprime.

- **Conversion, puis arrêt** :
  - **notice source** : parmi les candidats Sudoc retenus, celui du
    `thesis_state` retenu avec le meilleur score ; sa notice UNIMARC est lue
    via `https://www.sudoc.fr/{ppn}.xml` et convertie au format WinIBW ;
  - **conversion en v1** : copie de la notice source, avec l'alerte
    `conversion_non_implementee` et la notice cible affichée (`conversion/`,
    remplaçable sans toucher au workflow) ;
  - **conservation** (ALM-SUD-F-002) : notice source et notice convertie sont
    stockées dans le dossier et téléchargeables.
- **Traçabilité** : à chaque étape, le dossier journalise les valeurs lues,
  le chemin suivi dans l'arbre et le résultat ; l'onglet du dossier affiche le
  **parcours du pipeline**.
- **Reprise** (document à reprendre) : actions fixes de la carte « Bilan » du dossier —
  choisir EPHE / hors EPHE (provenance) ; **ignorer le résultat (faux positif)
  et poursuivre** (ALM-SUD-006 : Alma → `no_academic_record_found`, Sudoc →
  `no_print_duplicate_found`, sans rappeler le service) ; **confirmer que la
  notice existe déjà** (arrêt Alma ou Sudoc : le dossier passe à « terminé »,
  décision du 11/11) ; choisir le PPN IdRef ; « traité hors outil ».

## 8. Règles et générateur

### 8.1 Où sont les règles

Décision du 08/11 : les règles sont éditées dans le **studio de catalogage**
(§9.3) ; les catalogueurs manipulent l'arbre du pipeline et des
zones de notice, jamais des expressions ni des chaînes UNIMARC à conditions.
Le studio écrit des fichiers YAML versionnés, validés avant enregistrement.

| Fichier | Contenu |
|---|---|
| `regles/variables.yaml` | Dictionnaire des variables : `source`, `type` (texte, personne, organisme), `liste`, `disponible`, `description` |
| `regles/pipeline.yaml` | Pipeline (§7) : étapes, variables lues (codes des API), arbre de décision et résultats |
| `regles/notice/socle.yaml` | Socle commun de la notice : zones structurées, rendu des autorités |
| `regles/notice/variantes/*.yaml` | Variantes (thèse originale imprimée, thèse de reproduction imprimée, mémoire original imprimé) : zones ajoutées, remplacées, retirées |

Le dossier des règles est `REGLES_DIR` (`regles` par défaut). Le bouton
« Recharger les règles » les relit sans redémarrer ; la version chargée (date
et empreinte des fichiers) est enregistrée dans chaque dossier. L'API
`GET /api/v1/regles` les sert tels quels. Les anciens profils WinIBW
(`regles/profils/`, lignes `ZZZ II$a{x} || repli`) ont été convertis
automatiquement en socle + variantes, sans écart sur 33 cas comparés
(11 contextes × 3 profils), puis supprimés ; rule-engine est retiré.

### 8.2 Notice structurée : socle, variantes, zones

Une **notice-modèle** (ce que désigne le `profil` d'une décision) = le socle +
une variante. Le socle réunit les zones communes ; une variante `ajouter` des
zones, en `remplacer` (même `id`) ou en `retirer`.

```yaml
# regles/notice/socle.yaml
rendus: {personne: ppn_sinon_nom_prenom, organisme: ppn_sinon_forme}
zones:
  - id: '328'
    zone: '328'
    libelle: Note de thèse
    indicateurs: '#0'
    sous_zones:
      - {code: b, libelle: Diplôme, variable: diplome}
      - {code: d, libelle: Année, variable: annee, si_absente: repli, repli: '19..'}
    priorite: 1
    à_valider: true
  - id: 200
    sous_zones:
      - {code: g, variable: directeur, prefixe: 'sous la direction de ', liste: joindre,
         separateur: ', ', dernier: ' et '}
  - id: 700-auteur
    zone: '700'
    indicateurs: '#1'
    sous_zones: [{code: '3', autorite: auteur}, {code: '4', constante: '070'}]
```

Chaque **sous-zone** a une seule sorte de valeur :

| Valeur | Propriétés |
|---|---|
| constante | `constante` |
| donnée du document | `variable` (+ `attribut` : forme, nom, prenom, ppn), `prefixe`, `suffixe` ; variable liste : `liste: repeter` (une sous-zone par valeur) ou `joindre` (`separateur`, `dernier`) |
| composition | `parties` : textes fixes et variables mis bout à bout (029 $b = année + code établissement + numéro NNT) |
| autorité | `autorite` (personne ou organisme) ; `rendu` : mode partagé du socle par défaut |

Et, sauf pour une constante, **si la valeur est absente** (`si_absente`) :
ne pas produire la sous-zone (`omettre`), utiliser une valeur de repli
(`repli`), ou **demander une vérification humaine** (`verification` : la
sous-zone est omise, la notice produite, le document passe « à reprendre »
avec une exception).

**Rendus d'autorité** (implémentés en Python, choisis dans la configuration) :
`ppn_sinon_nom_prenom` ($3 si aligné, sinon $a nom $b prénom),
`ppn_sinon_forme`, `nom_prenom`, `forme`. Ils remplacent les replis `||`
répétés dans chaque zone 7xx.

Une **zone** porte `id` (unique, référencé par les variantes), `zone`
(étiquette), `indicateurs` (absents : zone de contrôle), `libelle`,
`priorite`, `à_valider`, `actif` (`false` : documentée mais non produite),
`remarque`.

### 8.3 Règles de production

1. **Répétition** : une zone dont une sous-zone est une autorité est produite
   une fois par personne ou organisme (zéro s'il n'y en a pas).
2. **Sous-zone répétée** : une variable liste répète la sous-zone pour chaque
   valeur, sauf `liste: joindre`.
3. **Donnée manquante** : selon `si_absente` (omettre, repli, vérification).
4. **Zone vide** : si toutes les sous-zones à variable sont absentes et qu'une
   au moins n'a pas de repli, la zone est omise ; une zone sans variable est
   toujours produite.
5. **Priorité 1** : une zone `priorite: 1` qui ne produit rien déclenche une
   alerte en clair : « zone 029 obligatoire non produite (029 (these-originale-imprimee)) ».
6. **`actif: false`** : jamais produite, listée comme « non couverte ».
7. **Ordre** : zones triées par étiquette (ordre du socle, puis des ajouts, à
   étiquette égale) ; sous-zones dans l'ordre configuré.

Chaque zone produite garde la trace de sa provenance (`id (socle)` ou
`id (variante)`, et « à valider » le cas échéant), affichée en annotation dans
le dossier, l'éditeur et le banc d'essai. Décisions du 06/11 : 029$b =
`{annee}{code_etab}{numero_nnt}` ; aucune zone 600 à 607 n'est produite.

### 8.4 Vérificateur

`humatheque check-spec`, exécuté au démarrage, à chaque rechargement et avant
chaque enregistrement depuis le studio (§9.3), affiché dans l'interface.

| Gravité | Contrôle |
|---|---|
| erreur | YAML invalide (fichier, ligne) ; structure attendue absente ; propriété inconnue |
| erreur | Étiquette hors 3 chiffres ; indicateurs hors 2 caractères ; code de sous-zone ; identifiant de zone en double |
| erreur | Sous-zone sans valeur (zone active) ou avec deux sortes de valeur ; repli choisi sans valeur ; mode de rendu inconnu |
| erreur | Variable absente de `variables.yaml` ; attribut impossible pour le type ; autorité qui n'est ni personne ni organisme ; zone répétée sur deux autorités |
| erreur | Variante qui remplace ou retire une zone absente du socle, ou ajoute une zone déjà dans le socle |
| erreur | Pipeline : variable non lue par l'étape ; valeur absente des valeurs possibles ; résultat inconnu ; branche avec à la fois sous-branches et résultat (ou ni l'un ni l'autre) ; notice cible inconnue |
| avertissement | Notice (variante) atteinte par aucune branche |
| info | **Cas qui tombent dans « sinon »** : combinaisons des valeurs possibles d'une étape, réduites aux variables qui comptent |
| info | Variable utilisée par aucune zone active |

Avec les règles actuelles, 3 cas Sudoc tombent dans « sinon » (stop à
reprendre) : `printed_reproduction` hors EPHE, `printed_original` à l'EPHE
(impossible), `electronic` avec `duplicate_found`.

### 8.5 Sorties

**Sorties** : la notice est affichée et éditée au format WinIBW (une zone par
ligne, triée par étiquette) ; elle se télécharge en **ISO 2709** (`.mrc`) et
en **UNIMARC/XML** (`.xml`, UTF-8), via `pymarc` (décision du 09/11 : plus
d'export texte WinIBW). Pour une conversion, la notice source Sudoc se
télécharge aussi dans les deux formats. Le nom du fichier reprend
l'identifiant du document, en ASCII et sans l'extension de l'image.
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
  jetons de couleur repris de la maquette (palette, pastilles d'issue), une
  seule feuille compilée dans `static/`. Soigner les états vides, de
  chargement et d'erreur ; pastilles d'issue (Terminé, À reprendre, Incident)
  identiques sur tous les écrans.
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
    sous-zones produites par aucune ligne du profil (avertissement), PPN mal
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

- **Gabarit commun** (décision du 11/11) : bandeau supérieur « Humathèque POC
  catalogage » et rail latéral (badge H, Accueil, Traiter, Lots, Historique,
  Admin).
- **`/` Accueil** (décisions du 11/11 et du 12/11) : une entrée de menu par page, présentée comme un lien (icône, libellé, flèche) et non comme un indicateur (Lots traités —
  lots, documents, à reprendre ; Historique ; Chargement manuel ;
  Administration — état des règles), les derniers traitements et le
  raccourci « Balayer le bucket » (liste des dossiers du bucket MinIO avec
  leur dernier statut et sélection multiple, ING-F-005) ; bannière des lots
  automatiques créés depuis la dernière visite.
- **`/traiter` Nouveau traitement (chargement manuel)** : le formulaire du §3,
  l'état de la spec chargée et les avertissements du vérificateur. Si le
  fournisseur VLM est un endpoint Hugging Face dédié : indicateur d'état et
  bouton de réveil de l'endpoint (repris du POC).
- **Administration** : deux pages.
  - **`/admin` Services et déclencheurs** (décisions du 05/11) : santé des
    services (alma, sudoc, idref, dewey, VLM, stockage S3, règles),
    réglages des lots automatiques (fournisseur, paramètres avancés ; proposés
    aussi par défaut dans le formulaire), **modèles par fournisseur** (URL,
    clé configurée ou non — jamais affichée —, modèle de `.env`, choix d'un
    autre modèle dans la liste renvoyée par l'API du fournisseur ; décision du
    11/11), déclencheurs (§3.1), état du planificateur.
  - **`/admin/prompts` Prompts** (décision du 11/11) : prompt des thèses et des
    mémoires dans l'éditeur, enregistrement vérifié (schéma JSON lisible),
    archivé et journalisé ; à côté, « Du prompt à la notice » : champ du
    schéma → variable de la notice → transformation, **modifiable** (nom de
    la variable, liste, active ; réécrit `regles/variables.yaml` après
    vérification), puis les variables calculées ou fournies par un service.
    Un champ ajouté au prompt (ex. `abstract`) est conservé par l'extraction
    et transmis tel quel au contexte : il suffit de lui donner une variable
    active pour l'utiliser dans le studio (ex. zone 330 $a).
  - **`/admin/regles` Studio de catalogage** (décisions du 08/11 et du 09/11) :
    - **Pipeline** : les étapes numérotées, chacune avec ce qu'elle lit et son
      arbre indenté (SI … ET … OU … SAUF … ⟶ résultat → étape suivante ;
      SINON) ; chaque branche se modifie sur place (✎), on ajoute une branche
      après (+) ou une sous-branche (⤷), on monte, descend, supprime ; les
      valeurs proposées sont les codes de l'API de l'étape ; **les étapes
      elles-mêmes se déplacent** (↑ ↓, décision du 11/11) à l'intérieur de
      leur groupe — vérifications (provenance, Alma, Sudoc), puis
      enrichissements (IdRef, Dewey), création toujours à la fin. L'ordre du
      fichier est l'ordre d'exécution du workflow (noté dans le dossier,
      champ `ordre`). Le vérificateur refuse une étape qui teste `ephe` avant
      la provenance, et un mélange des groupes ;
    - **Notice UNIMARC** : socle commun et variantes ; liste des zones ;
      éditeur de zone (sous-zones listées puis paramétrées : sorte de valeur,
      donnée, texte avant / après, plusieurs valeurs, si absente : omettre,
      repli, vérification humaine), aperçu immédiat ; rendu des autorités ;
    - **Alertes** (décision du 11/11) : pour chaque famille d'alertes
      (pipeline, zone obligatoire, conversion, année illisible, $ dans une
      valeur, extraction divergente, document invalide, langue sans articles,
      anciennes alertes, autres), le libellé affiché et si elle est montrée
      (`regles/alertes.yaml`). Le dossier affiche les alertes regroupées
      (« Non trouvé dans IdRef : Ivar Ekeland (président du jury), … ») ; elles
      restent toutes dans le journal et le rapport CSV. Les anciennes alertes
      (accents, mots inconnus, bouchon, proto-notice) sont masquées ;
    - **Banc d'essai** : un dossier réel (codes repris) ou l'exemple fictif
      avec des valeurs choisies → parcours du pipeline étape par étape,
      données préparées, notice avec la provenance de chaque zone ;
      **non-régression** sur les notices déjà produites ;
    - **Fichiers** (experts) : YAML dans Monaco, vérifier / enregistrer.

    Chaque enregistrement réécrit un seul fichier, refuse toute erreur
    bloquante du vérificateur, archive la version précédente dans
    `regles/archives/<horodatage>/`, journalise (qui, quand, fichier) et
    recharge les règles.
- **Mot de passe** : `MOT_DE_PASSE` dans `.env` protège toute l'application
  (pages par cookie de session, API par `Authorization: Bearer`) ; vide =
  désactivé (état actuel).
- **`/historique`** (ING-F-006, ING-T-004) : balayages et traitements, avec date,
  déclencheur, nombre de documents, issues, lien vers chaque lot.
- **`/lots/{id}` Lot** (mode 2) :
  - les indicateurs de la maquette : documents, passés seuls, en attente,
    repris ;
  - les filtres À reprendre / Incidents ;
  - la file d'exceptions (document, étape, motif, bouton Reprendre) ;
  - le journal ;
  - l'export : CSV du rapport, et toutes les notices produites en ISO 2709 ou
    en UNIMARC/XML (collection) ;
  - boutons « ← Retour au lot » et « ← Tous les lots » en tête du dossier, de
    l'aperçu et du lot ; la page Lots indique aussi le nombre de **documents
    traités** (lots affichés) ;
  - un seul bouton **« Voir »** par document, qui ouvre le dossier (décision
    du 09/11) ; l'aperçu en lecture seule reste accessible depuis le dossier.
- **`/dossiers/{id}` Dossier** (mode 1, et cible de « Reprendre »). Mise en
  page en vis-à-vis :
  - **panneau gauche** : les images du document, avec défilement d'une image à
    l'autre ;
  - **panneau droit, haut** : les métadonnées extraites ;
  - **panneau droit, bas** : les informations du traitement (date, modèle,
    statut), le motif de l'arrêt s'il y a lieu (ALM-SUD-F-005), les sorties
    détaillées de chaque brique, et le bouton « Relancer » (tout le pipeline) ;
  - pour le résultat final, la notice UNIMARC en vis-à-vis des images, dans
    l'éditeur (§9.2) ;
  - pour une conversion : images à gauche, notice Sudoc avant transformation en
    haut à droite, notice produite par la conversion en bas à droite, éditable.

  **Organisation du panneau droit** (décision du 11/11, après les premiers
  essais) — l'information utile d'abord, le reste à titre informatif :
  - **carte « Bilan »**, toujours en haut : issue, étape et motif écrit dans le
    pipeline, branche suivie ; puis les pièces selon le cas (arrêt Alma : les
    notices candidates — PPN, MMS id, titre, auteurs, année, type, score ;
    arrêt Sudoc : les candidats retenus avec `thesis_state` ; provenance :
    établissement lu et règle ; conversion : PPN source et notice cible ;
    notice produite : profil, alertes, **points à valider** ; incident :
    service, appel, erreur, « Relancer depuis cette étape ») ; puis **les
    actions de reprise** (il n'y a plus d'onglet « Reprise ») : faux positif,
    notice déjà présente, choix de provenance, choix IdRef groupés avec un
    seul bouton de validation, classe Dewey, « traité hors outil » ;
  - **onglets** : **Notice** (seulement s'il y a une notice ; ouvert par
    défaut), **Extraction** (sortie brute du modèle en tête, puis champs
    extraits et normalisés, provenance, transformations), **Services** (une
    carte par étape dans l'ordre d'exécution : résultat dans le pipeline,
    pièces, requêtes et réponses brutes ; étapes non exécutées grisées ;
    ouvert par défaut sans notice), **Journal** (parcours du pipeline, puis
    journal) ;
  - la frise des étapes est cliquable : elle mène à la carte de l'étape dans
    « Services » (l'étape d'arrêt est entourée).

  Contenu :
  - une frise des étapes avec statut et durée ;
  - l'extraction brute, puis normalisée, avec le diff des transformations du §4 ;
  - le contexte ;
  - la règle de décision retenue (identifiant, condition, faits) ;
  - les candidats Alma, Sudoc, IdRef et Dewey avec liens, et pour les notices
    trouvées leurs informations principales : PPN, titre, auteur, année, type
    (ALM-SUD-F-004) ;
  - les réponses brutes des API, repliées par défaut et dépliables
    (ALM-SUD-F-003) ;
  - la notice, chaque zone annotée par sa provenance (zone du socle ou de la variante, « à valider ») ;
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
  - `REGLES_DIR=regles` ;
  - `CODE_ETAB=EPHE`, `MAX_WORKERS` (les seuils des services sont dans
    `config/services.yaml`) ;
  - `VLM_MODEL=google/gemma-4-26B-A4B-it`, `VLM_STRATEGIE=requete_unique`,
    `VLM_TIMEOUT`, `HTTP_TIMEOUT`, `RELANCES_AUTO=2` ;
  - `PROVENANCE_CONFIRMATION_LLM=false` ;
  - `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_PREFIXE=poc/`, `MINIO_ACCESS_KEY`,
    `MINIO_SECRET_KEY`, `INGESTION_AUTO=false`, `BALAYAGE_INTERVALLE=60`,
    `PLANIFICATION_CRON=`, `EVENEMENTS_DELAI=60` ;
  - `MOT_DE_PASSE=` (vide : pas d'authentification) ;
- **Déploiement** :
  - `Dockerfile` : image `python:3.12-slim` avec `uv` ; l'exécutable autonome Tailwind est téléchargé (version
    figée) et compile la feuille de style au build. En développement :
    `uv run humatheque css --watch` ;
  - `docker-compose.yml` : **un service** `app`, construit depuis le
    `Dockerfile`, port 8000, volumes `./data` et `./regles` (les règles
    modifiées depuis l'interface restent dans le dépôt), `env_file: .env`.
    Grist est retiré (décision du 06/11) ;
  - en local : `uv run humatheque serve`, ou `docker compose up -d`.
  - VPS (décision du 11/11) : `deploy/vps/compose.catalogage.yml`, service à
    ajouter à la stack Traefik existante (réseau `web`, Let's Encrypt,
    `FORWARDED_ALLOW_IPS=*`), et `deploy/vps/catalogage.env.example` avec les
    adresses internes des services (`http://sudoc-checker:8000`,
    `http://minio:9000`, VLM llama.cpp…), dans un `.catalogage.env` distinct
    du `.env` de la stack ;
  - sauvegarde des règles modifiées sur le VPS (décision du 12/11) :
    `scripts/sauvegarde-regles.sh`, lancé chaque nuit par cron sur l'hôte,
    commite `regles/` et `config/prompts/` et les pousse vers la branche
    `regles-vps` (fusion dans `main` par PR). L'application n'a ni git ni
    accès en écriture au dépôt.

## 11. Tests et ordre de construction

Chaque étape se termine par `uv run pytest` vert. Services mockés avec `respx`
(réponses d'exemple tirées des README et OpenAPI).

1. **Socle** : `pyproject`, configuration, chargement des règles (`regles/`)
   et vérificateur, en partant de `reference/moteur-regles/` (décision du
   06/11 : les règles quittent Grist). Tests : `check-spec` sur `regles/` ne
   signale aucune erreur et exactement les 4 cas non couverts du §8.4 ; un
   fichier de règles fabriqué par défaut du tableau du §8.4.
2. **`texte_unimarc`** : toutes les tables de cas du §4. **Provenance** : table
   de cas (« École pratique des hautes études », « EPHE », « E.P.H.E. Ve
   section » → EPHE ; « Université Paris 1 » → hors EPHE ; vide → absent ;
   « École des hautes études » → ambigu ; EPHE en cotutelle seulement → ambigu).
3. **Générateur** sur des contextes écrits à la main (thèse originale EPHE,
   reproduction hors EPHE, mémoire), en reprenant `reference/moteur-regles/tests/`.
   Tests de référence : 105 en une seule occurrence (`$bm$ba` ou `$bv$ba`), 328
   avec `$zReproduction de` en tête pour la reproduction, une 701 par
   rapporteur, repli `$a$b` sans PPN, un seul 200$g « A et B ».
4. **Pipeline** : un test par branche de `pipeline.yaml` (codes des API),
   plus `sinon`, la lecture qui continue après un embranchement sans issue
   et « conversion puis arrêt ».
5. **Clients des services, agrégation `thesis_state` et workflow** de bout en bout
   (mocks). Agrégation testée par table : originale + reproduction → originale ;
   reproduction + unknown → unknown ; originale + unknown → originale ;
   électronique seule ; aucun retenu + `ambiguous_print_candidate` → ambigu ;
   `thesis_state` absent → incident.
   « Thèse EPHE absente du Sudoc » produit une notice ; « Notice déjà dans
   Alma » s'arrête avec le PPN Alma ; « Thèse EPHE, notice électronique au
   Sudoc » produit une notice convertie
   identique à la source, avec l'alerte `conversion_non_implementee`, et
   s'arrête ; une provenance ambiguë arrête (à reprendre) puis reprend après
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
7. **Docker Compose** : `docker compose up -d` démarre l'application (Grist,
   présent à l'origine, est retiré le 06/11). Puis essai réel sur 5 URL de
   `minio_urls.txt` et sur 5 dossiers du bucket `poc/`. Consigner les écarts dans `docs/ESSAI.md`.
8. **Détection automatique et planification** (ING-F-003, ING-F-004, arbitrées
   le 05/11) : page d'administration, planificateur, mot de passe prévu.

## 12. Traçabilité avec les exigences du wiki

| Exigence wiki | Section | État |
|---|---|---|
| ING-F-001, ING-F-002 | §3.1 | intégrée |
| ING-F-003, ING-F-004 | §3.1, §9.3, §11 étape 8 | intégrée (05/11) : trois déclencheurs au choix, désactivés par défaut |
| ING-F-005, ING-F-006 | §3, §3.1, §9 | intégrée |
| ING-F-007 | §6 | déjà couverte |
| ING-F-008 | §3.1 | intégrée |
| ING-F-009 | §3 | intégrée (documents du bucket) |
| ING-T-001, VLM-T-001 | §5 | intégrée |
| ING-T-002, VLM-T-002 | §5 | intégrée (la zone 330 reste `actif: false` dans les profils) |
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
| Conversion « reproduction via 105 $bm, EPHE » | — | coquille du wiki : la branche Sudoc `printed_reproduction` ET `ephe = oui` de `pipeline.yaml` fait foi |
| Mémoire trouvé au Sudoc | §7 | la spec fait foi : conversion si notice électronique (« Mémoire, notice électronique au Sudoc ») |
