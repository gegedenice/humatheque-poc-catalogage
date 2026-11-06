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
  regles/                   règles de catalogage (§8) : variables.yaml, profils/*.yaml, decisions.yaml
  reference/                POC, maquette, mindmap, moteur-regles/ (lecture seule)
  src/humatheque/
    config.py               settings pydantic (env), chemins, seuils
    cli.py                  serve | check-spec | run-url | run-batch | scan-minio | css
    ingestion/              lecture du bucket MinIO, constitution des documents (§3)
    texte_unimarc/          normalisation de chaînes, « module NOR » du wiki (§4)
      _vendor/reaccentue/   reaccentue vendorisé (+ dico/)
    extraction/             appel VLM multi-images, fournisseurs interchangeables
  config/prompts/           prompts d'extraction externalisés (VLM-T-005)
    contexte/               adaptateur extraction → Contexte + fonctions de calcul
    services/               clients HTTP : alma, sudoc, idref, dewey
    qualification/          agrégation des thesis_state Sudoc en état du document (pas d'appel réseau)
    regles/                 chargement des règles YAML, moteur, vérificateur
    decision/               décisions (rule-engine), issue des réponses de services, provenance
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
   y compris après un trait d'union (`Jean-Daniel`). Accent (décision du
   05/11) : un mot imprimé en capitales sans accent reçoit l'accent si le
   dictionnaire ne connaît qu'une forme de ce nom propre, accentuée
   (`HELENE` → `Hélène`, alerte `accent_restitue`) ; sinon il reste sans
   accent, avec l'alerte `accent_possible` s'il existe une forme accentuée.
5. Forme retraitée (décision du 05/11) : `forme` = « Prénom Nom » après ces
   traitements (`Monsieur le Professeur André GODIN` → `André Godin`) ; c'est
   elle qu'utilisent 200$f et 200$g. La forme imprimée (civilités retirées)
   est conservée pour la trace.

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
  produit les valeurs normalisées du document (clés du schéma d'extraction et
  valeurs calculées) ; les **variables** lues par le générateur portent les
  noms de `regles/variables.yaml` (`titre`, `auteur`, `directeur`, `annee`…).
  La colonne `source` de ce fichier indique d'où vient chaque valeur
  (`extraction.defense_year`, `calcul`, `constante`, `service.dewey-classifier`) :
  l'adaptateur s'en sert pour passer des noms du schéma d'extraction aux noms
  des profils (une source non `extraction.*` porte le nom de la variable).
  - **Champs simples** : on applique la casse du §4 selon le champ (`title` →
    casse_phrase, `subtitle` → casse_complement_titre).
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

   `absent` et `ambigu` arrêtent le document (issue « à reprendre », motif
   « provenance à confirmer ») ; le catalogueur choisit EPHE ou hors EPHE et le
   pipeline reprend à l'étape 3. La règle appliquée et la forme comparée sont
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
   - un candidat retenu `unknown` donne `indéterminé` (issue « à reprendre »,
     motif « nature de la notice Sudoc non qualifiable ») : cette notice
     pourrait être l'originale, on ne tranche pas à sa place. **Exception** :
     si une originale imprimée est déjà retenue, l'`unknown` ne change rien et
     l'état reste `originale imprimée` ;
   - aucun candidat retenu : `ambigu` si le statut du service est
     `ambiguous_print_candidate`, sinon `rien` ;
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

Le dossier mémorise les résultats de chaque étape. **Relancer un dossier**
repart de la première étape en échec ou non faite ; les étapes réussies ne sont
pas rappelées, sauf demande explicite (ING-F-007).

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

## 7. Décision

- **Faits de décision** : `type` (thèse, mémoire), `provenance` (EPHE, hors
  EPHE, incertaine, sans objet pour un mémoire), `alma` (présent, incertain,
  absent : correspondance du §6), `sudoc` (état agrégé du §6 ; `non vérifié`
  quand Alma a trouvé une notice). Leurs valeurs possibles sont déclarées dans
  `regles/decisions.yaml` (`faits:`).
- **Moteur** : `regles/decisions.yaml`, règles lues dans l'ordre, la première
  dont la condition est vraie l'emporte ; `sinon` est obligatoire. Les
  conditions sont des expressions `rule-engine` (`==`, `!=`, `in [...]`, `and`,
  `or`, `not`, comparaisons numériques), ce qui permet d'ajouter plus tard des
  faits produits par un modèle de décision (`qualite_extraction < 0.6`).
- **Règles nommées** : chaque règle porte un `nom` en clair, qui sert
  d'identifiant (unique) et s'affiche dans le dossier, le rapport et
  l'administration (« Notice déjà dans Alma », « Thèse EPHE absente du Sudoc »…).
  `à_valider: true` signale une règle encore en discussion (décision du 06/11 :
  plus d'identifiants hérités de Grist).
- **Actions et issue** : l'issue du document découle de l'action, sans champ
  `routage` (décision du 06/11) :

  | Action | Effet | Issue |
  |---|---|---|
  | `création` (profil) | la notice est produite | Terminé |
  | `conversion` (profil cible) | la notice Sudoc est dérivée | Terminé |
  | `arrêt` (motif) | rien à produire (notice déjà au catalogue) | Terminé |
  | `vérification` (motif) | un catalogueur doit trancher | À reprendre |

  Une panne technique donne l'issue **Incident**, sans règle. Décision du
  05/11 : une notice trouvée au-dessus du seuil arrête sans alerte (terminé) ;
  seul le cas incertain est à reprendre (Alma `incertain`, Sudoc `ambigu` /
  `indéterminé`).
- **`sinon`** : action `vérification`, motif « Situation non prévue », faits affichés.
- **Action `conversion` : conversion, puis arrêt** (thèses et mémoires) :
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
- Une action `arrêt` ou `vérification` arrête sans conversion. L'onglet
  « Reprise » du dossier (faux positif, traité hors outil) n'apparaît que pour
  un document à reprendre ou en incident.
- **Réponses des services** : `config/services.yaml` associe, pour chaque
  service, son statut à une issue (`statuts`), une alerte éventuelle
  (`alertes`) et le caractère non bloquant (`non_bloquant`). Une erreur HTTP
  (4xx/5xx), un service injoignable (`service_indisponible`) ou un statut
  absent du fichier donnent « incident ». **alma-checker et sudoc-checker**
  n'ont pas de `statuts` : ils sont jugés par les états du §6 et
  `decisions.yaml`. Pour IdRef et Dewey (`non_bloquant: true`), « à reprendre »
  **n'arrête pas** la génération : on utilise le repli (`$a{nom}$b{prenom}` ou
  zone omise) et une exception est créée. `not_found` côté IdRef ajoute l'alerte
  « proto-notice d'autorité à créer ». (Décision du 06/11 : l'ancienne table
  Grist `Statuts_services` est retirée.)
- **Issue du dossier** : la plus sévère de ses étapes, dans l'ordre
  `incident > à reprendre > terminé`. Les dossiers enregistrés avec l'ancien
  routage sont relus avec la nouvelle issue (auto, sortie, conversion → terminé ;
  décision → à reprendre).

## 8. Règles et générateur

### 8.1 Où sont les règles

| Fichier | Contenu |
|---|---|
| `regles/variables.yaml` | Dictionnaire des variables : `source`, `type` (texte, personne, organisme), `liste`, `disponible`, `description` |
| `regles/profils/these-originale-imprimee.yaml` | Notice-modèle de la thèse originale imprimée |
| `regles/profils/these-reproduction-imprimee.yaml` | Notice-modèle de la thèse de reproduction imprimée |
| `regles/profils/memoire-original-imprime.yaml` | Notice-modèle du mémoire original imprimé |
| `regles/decisions.yaml` | Faits, règles de décision, `sinon` |

Le dossier des règles est `REGLES_DIR` (`regles` par défaut). Les fichiers
sont versionnés avec le code. Le bouton « Recharger les règles » les relit sans
redémarrer ; la version chargée (date et empreinte des fichiers) est
enregistrée dans chaque dossier. L'API `GET /api/v1/regles` les sert tels
quels. La référence d'implémentation est `reference/moteur-regles/` (moteur,
exemple de contexte, tests sur les règles réelles). En cas d'écart UNIMARC
entre cette référence et l'ancien classeur Grist, le classeur a fait foi lors de
la migration (décision du 06/11) ; Grist est ensuite retiré du projet.

### 8.2 Format d'un profil

```yaml
profil: these-originale-imprimee
libelle: Thèse originale imprimée
variables: [annee, langue, titre_classement, auteur, directeur, ...]
lignes:
  - "008 $aAax3"
  - {ligne: "100 0#$a{annee} || 100 0#$a19XX", priorite: 1, à_valider: true}
  - {ligne: "200 1#$a{titre_classement}$f{auteur}$gsous la direction de {directeur}",
     joindre: {directeur: [", ", " et "]}}
  - {ligne: "700 #1$3{auteur.ppn}$4070 || 700 #1$a{auteur.nom}$b{auteur.prenom}$4070", priorite: 1}
  - {ligne: "330 ##$a{resume}", actif: false}
```

- Une ligne est une zone écrite comme dans WinIBW (`ZZZ II$a…`, `#` = blanc ;
  sans indicateurs : zone de contrôle). Forme courte : une chaîne. Forme
  développée : `ligne`, et au besoin `si` (condition rule-engine sur les
  variables), `priorite`, `remarque`, `joindre`, `à_valider` (`true` : ligne
  encore en discussion ; absent : validée) et `actif` (`false` : zone documentée
  mais non produite). Décision du 06/11 : plus de `ref` ni de `statut` hérités
  de Grist.
- `{variable}` ou `{variable.attribut}` (`forme`, `nom`, `prenom`, `ppn` pour une
  personne ; `forme`, `ppn` pour un organisme). `{x}` seul vaut `{x.forme}`.
- `joindre: {variable: [séparateur, dernier séparateur]}` (ou un seul
  séparateur) : la variable liste est jointe dans **une seule** sous-zone au lieu
  de répéter la sous-zone. Décision du 06/11 : 200$g = `sous la direction de A
  et B` (`A, B et C` pour trois directeurs).
- `variables` liste les variables du profil ; le vérificateur la compare aux
  variables réellement utilisées.

### 8.3 Règles de production

1. **Répétition** : une ligne qui utilise un attribut d'une personne ou d'un
   organisme est produite une fois par élément (zéro s'il n'y en a pas).
2. **Sous-zone répétée** : une variable liste utilisée seule (`$e{sous_titre}`)
   répète la sous-zone pour chaque valeur, sauf si elle est dans `joindre`.
3. **Donnée manquante** : la ligne de repli (`||`) est utilisée si elle existe ;
   sinon seules les sous-zones concernées sont omises.
4. **Ligne vide** : si toutes les sous-zones à variable sont omises, la zone est
   omise ; une ligne sans variable est toujours produite.
5. **Priorité 1** : une ligne `priorite: 1` qui ne produit rien déclenche une
   alerte en clair : « zone 029 obligatoire non produite (ligne 2) ».
6. **`actif: false`** : jamais produite, listée comme « non couverte ».
7. **`si`** : la ligne n'est produite que si la condition est vraie.
8. **Ordre** : zones triées par étiquette ; sous-zones dans l'ordre de la ligne.

Chaque zone produite garde la trace de sa ligne (zone et numéro, « 105 (ligne 7) »,
et « à valider » le cas échéant), affichée en
annotation dans le dossier et l'éditeur. Décisions du 06/11 sur les écarts avec
l'ancienne référence : 029$b = `{annee}{code_etab}{numero_nnt}` ; aucune zone
600 à 607 n'est produite (R040, ligne inactive).

### 8.4 Vérificateur

`humatheque check-spec`, exécuté au démarrage, à chaque rechargement et avant
chaque enregistrement depuis l'interface (§9.3), affiché dans l'interface.

| Gravité | Contrôle |
|---|---|
| erreur | YAML invalide (fichier, ligne) ; structure attendue absente |
| erreur | Zone sans étiquette sur 3 chiffres |
| erreur | Variable absente de `variables.yaml` ; attribut impossible pour le type |
| erreur | Ligne qui se répète sur deux variables différentes ; `joindre` sur une variable qui n'est pas une liste de la ligne |
| erreur | Condition rule-engine invalide (profil ou décision) ; profil inconnu dans une décision ; action inconnue ; nom de règle de décision en double |
| avertissement | Variable utilisée mais absente de la liste `variables` du profil |
| avertissement | Profil atteint par aucune décision |
| avertissement | **Cas non couvert** : combinaison des valeurs de faits (Alma absent) qui tombe dans `sinon` |
| info | Variable déclarée dans un profil mais utilisée par aucune ligne active |

Avec les règles actuelles, le contrôle de complétude doit signaler 4 cas :
thèse EPHE avec originale imprimée au Sudoc ; mémoire avec originale,
reproduction imprimée ou reproduction microfiche au Sudoc.

### 8.5 Sorties

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

- **`/` Nouveau traitement** : le formulaire du §3, la liste des dossiers du
  bucket MinIO avec leur dernier statut et sélection multiple (ING-F-005),
  l'état de la spec chargée et les avertissements du vérificateur. Si le
  fournisseur VLM est un endpoint Hugging Face dédié : indicateur d'état et
  bouton de réveil de l'endpoint (repris du POC).
- **Administration** : deux pages.
  - **`/admin` Services et déclencheurs** (décisions du 05/11) : santé des
    services (alma, sudoc, idref, dewey, VLM, stockage S3, règles),
    réglages des lots automatiques (fournisseur, paramètres avancés ; proposés
    aussi par défaut dans le formulaire), déclencheurs (§3.1), état du
    planificateur.
  - **`/admin/regles` Règles** (décision du 06/11), en onglets :
    **Décisions** (tableau des règles, banc d'essai : faits saisis ou repris
    d'un dossier → règle retenue et évaluation de chaque condition ; cas non
    couverts), **Profils** (notice-modèle ligne à ligne, aperçu en direct de la
    notice produite sur le contexte d'un dossier ou d'un exemple), **Variables**
    (dictionnaire) et **Modifier** (fichier YAML dans Monaco, texte et
    commentaires conservés). « Vérifier » lance le vérificateur sur le
    brouillon ; « Enregistrer » refuse toute erreur bloquante, archive la
    version précédente dans `regles/archives/<horodatage>/`, journalise
    (qui, quand, fichier) et recharge les règles.
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
  - la règle de décision retenue (identifiant, condition, faits) ;
  - les candidats Alma, Sudoc, IdRef et Dewey avec liens, et pour les notices
    trouvées leurs informations principales : PPN, titre, auteur, année, type
    (ALM-SUD-F-004) ;
  - les réponses brutes des API, repliées par défaut et dépliables
    (ALM-SUD-F-003) ;
  - la notice, chaque zone annotée par sa ligne de profil (zone, numéro, « à valider ») ;
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
  - `Dockerfile` : image `python:3.12-slim` avec `uv` ; le cache reaccentue est
    construit au build ; l'exécutable autonome Tailwind est téléchargé (version
    figée) et compile la feuille de style au build. En développement :
    `uv run humatheque css --watch` ;
  - `docker-compose.yml` : **un service** `app`, construit depuis le
    `Dockerfile`, port 8000, volumes `./data` et `./regles` (les règles
    modifiées depuis l'interface restent dans le dépôt), `env_file: .env`.
    Grist est retiré (décision du 06/11) ;
  - en local : `uv run humatheque serve`, ou `docker compose up -d`.

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
4. **Décision** : une ligne de test par règle de `decisions.yaml`, plus
   `sinon` et « conversion puis arrêt ».
5. **Clients des services, agrégation `thesis_state` et workflow** de bout en bout
   (mocks). Agrégation testée par table : originale + reproduction → originale ;
   reproduction + unknown → indéterminé ; originale + unknown → originale ;
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
| Conversion « reproduction via 105 $bm, EPHE » | — | coquille du wiki : la règle « Thèse EPHE, reproduction au Sudoc » de `decisions.yaml` fait foi |
| Mémoire trouvé au Sudoc | §7 | la spec fait foi : conversion si notice électronique (« Mémoire, notice électronique au Sudoc ») |
