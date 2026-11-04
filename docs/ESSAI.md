# Essai réel — 04/11/2026 (SPEC §11, étape 7)

Conditions : VLM `google/gemma-4-26B-A4B-it:fastest` par Hugging Face Inference
Providers (`VLM_FOURNISSEUR=hf`, stratégie `requete_unique`) ; services
alma-checker, sudoc-checker, idref-linker, dewey-classifier de smartbiblia ;
stockage S3 `minio.smartbiblia.fr` ; spec `spec/*.csv` (empreinte
`1df30031c3c4`) ; paramètres des services de `config/services.yaml` (Dewey :
méthode `albert`) ; `SUDOC_DUPLICATE_THRESHOLD=0.78`, `IDREF_TIMEOUT=180`.

## 1. Cinq URL de `minio_urls.txt` (mémoires CESOR et CRALMI)

| Document | Résultat | Détail |
|---|---|---|
| memoires/CESOR/doc_01 (HDR, Paris I, 2008) | **sortie** S01 | Alma `ambiguous_academic_candidate`, PPN 152126031 → arrêt, Sudoc non appelé |
| memoires/CESOR/doc_06 (DEA, EHESS, 1993) | **décision** S12, notice produite | 2 exceptions IdRef `low_confidence` (repli `$a$b`) ; 711 EHESS alignée (026374889) ; Dewey 930 |
| memoires/CESOR/doc_11 (maîtrise, Paris XII, 2002) | **décision** S12, notice produite | 2 exceptions IdRef `low_confidence` ; titre « D'UN ROI L'AUTRE » → « D'un roi l'autre » |
| memoires/CESOR/doc_16 (DEA, Paris XII, 2002) | **incident** | idref-linker : délai de 180 s dépassé 3 fois (929 s) |
| memoires/CRALMI/Doc_03 (maîtrise, Paris I, 1999) | **décision** S12, notice produite | IdRef `not_found` ×2 → « proto-notice d'autorité à créer » ; Dewey 860 ; extraction 262 s (3 tentatives HF) |

Une première tentative avait échoué en totalité : `router.huggingface.co`
n'était pas encore autorisé par la politique réseau de l'environnement (403
au CONNECT), d'où cinq incidents `inference_indisponible` correctement relancés
puis signalés.

## 2. Cinq dossiers du bucket

Le bucket `poc` (structure du §3.1) ne contient qu'un dossier ; quatre
thèses du bucket `images` (structure réelle `theses/<labo>/<doc>/`)
complètent l'essai.

| Dossier | Images | Résultat | Détail |
|---|---|---|---|
| poc : TH/TH-00 (Paris 13, 2015) | 1 | **conversion** S03 | hors EPHE ; Sudoc : notice électronique 201289547 (0,84) ; notice source lue sur www.sudoc.fr, conservée en ISO 2709, copiée |
| images : theses/EPHESVT_theses/doc_01 (EPHE, 2014) | 2 (titre + résumé) | **incident** (S08 atteint) | provenance EPHE ; résumé extrait de la 2e image ; idref-linker : 180 s dépassés 3 fois |
| images : theses/EPHESVT_theses/doc_02 (EPHE, 2003) | 2 | **incident** | titre non extrait par le VLM → alma-checker `400 Unable to build Alma query` |
| images : theses/CESOR/doc_01 (Paris IV, 1995) | 1 | **sortie** S01 | Alma `academic_record_found` (notice sans PPN) |
| images : theses/theses.fr/2024PA01E001 (Paris 1, 2024) | 3 | **incident** (S09 atteint) | idref-linker : 180 s dépassés 3 fois |

## 3. Écarts constatés et suites données

### Corrigés pendant l'essai

1. **Identifiant d'un document par URL** : le nom de fichier (`page_de_titre.jpg`)
   se répète dans tout le bucket ; l'identifiant est désormais le chemin de l'URL.
2. **Civilités** : « Monsieur le Professeur André GODIN » donnait
   `$gsous la direction de le Professeur André GODIN` et `$ble Professeur André` ;
   « Mr » n'était pas reconnu. Ajout des mots de liaison (`le`, `la`), de
   `Mr`, `Mrs`, `Ms`, `Professeure`, `Docteure` (`config/texte_unimarc.yaml`).
3. **Sigles** : « … Familiale (PSF) » devenait « (psf) » ; dans un texte en
   casse mixte, un segment d'un seul mot en capitales est laissé intact.
4. **Composés** : « PANTHÉON-SORBONNE » devenait « panthéon-Sorbonne » ; une
   partie nom propre capitalise tout le composé.
5. **Relance automatique** : les exceptions d'une étape relancée
   s'accumulaient (IdRef ×3) ; chaque tentative repart désormais de zéro.
6. **Erreur 400** : relancée trois fois inutilement ; les erreurs 4xx ne sont
   plus relancées.
7. **Dépôt S3** : le workflow lisait les images avec le dépôt du `.env` et non
   avec celui qui avait constitué le lot (garde-fou : lecture refusée, aucune
   image lue dans le mauvais bucket).
8. **Structure du bucket** : `theses/` et `memoires/` (avec un niveau
   « labo ») au lieu de `TH/` et `MM/` ; ajout de ces noms dans
   `config/ingestion.yaml` (même principe : le dossier parent donne le type).

### Second passage IdRef (05/11)

Après le réglage d'idref-linker (environnement du service :
`IDREF_HTTP_TIMEOUT=8.0`, `IDREF_MAX_RETRIES=0`, `IDREF_MAX_CANDIDATES=5`,
`IDREF_MAX_DOCS_PER_ROLE=5` ; `config/services.yaml` : `max_candidates` et
`max_docs_per_role` = 5), les trois dossiers en incident IdRef ont été
relancés (relance limitée à l'étape en échec) :

| Dossier | Avant | Après |
|---|---|---|
| memoires/CESOR/doc_16 | incident, 3 × 180 s dépassés (929 s) | **27 s**, 2 personnes → décision S12, notice produite |
| theses/EPHESVT_theses/doc_01 | incident (740 s) | **53 s**, 8 personnes → décision S08, notice produite |
| theses/theses.fr/2024PA01E001 | incident (739 s) | **93 s**, 7 personnes → décision S09, notice produite |

Appels de 0,3 à 17 s par personne (contre 65 à plus de 180 s). Les statuts
restent surtout `low_confidence` / `not_found` : exceptions à trancher par
les catalogueurs, sans arrêt de la génération.

Décisions du 05/11 appliquées : titre non extrait → document invalide
(décision) ; forme retraitée « Prénom Nom » en 200$f et 200$g
(`$fChristelle Tesson$gsous la direction de Giovanni Stevanin`) avec accent
restitué quand le dictionnaire n'en connaît qu'une forme.

### À arbitrer (état au 04/11 ; points 1, 2 et 4 tranchés le 05/11)

1. **Lenteur d'idref-linker `/align/person`** (cause de 3 incidents sur 10) :
   environ 65 s par personne, parfois plus de 180 s ; une thèse a 5 à 8
   personnes. Options :
   - (a) réduire `max_candidates` / `max_docs_per_role` dans
     `config/services.yaml` (20 → 10) ;
   - (b) relever `IDREF_TIMEOUT` (300 à 600 s) ;
   - (c) traiter un délai dépassé sur **une** personne comme une exception non
     bloquante (repli `$a$b`, « IdRef indisponible pour X ») au lieu d'un
     incident du dossier — écart au §6 (« API indisponible → incident ») ;
   - (d) ne relancer que les personnes en échec. Proposition : (a) + (d), puis
     (c) si cela ne suffit pas.
2. **Titre non extrait** : le dossier part en incident sur une erreur 400
   d'alma-checker (champ `title` obligatoire). Proposition : arrêter avant Alma
   en `décision` (« titre non extrait, à vérifier »), comme un document
   invalide (ING-F-008).
3. **Alma `ambiguous_academic_candidate`** (HDR CESOR/doc_01) : arrêt S01
   conforme à la décision du 03/11 ; le faux positif se traite par la reprise
   manuelle (ALM-SUD-006).
4. **Forme des personnes en 200$g / 200$f** : transcrite telle qu'imprimée,
   capitales comprises (« Marie-France de LA ROCHE ») ; à confirmer par les
   catalogueurs.
5. **Dewey `albert`** : scores de l'ordre de 0,01 (reranker) ; codes parfois
   discutables (930 « Histoire ancienne » pour un réseau évangélique
   1864-1920). Le seuil `DEWEY_SEUIL` reste vide.
6. **`accent_ambigu:PARIS`** (Paris / Pâris) : conforme au §4.1, mais
   fréquent sur les établissements.

## 4. Docker Compose et Grist

- Image construite et lancée dans l'environnement de développement :
  `check-spec` dans le conteneur, service web (port 8000), CSS et cache de
  réaccentuation construits au build. Deux contraintes de cet environnement,
  sans effet sur le `Dockerfile` livré : Docker Hub limitait les
  téléchargements (429, image tirée d'un miroir pour l'essai) et `ghcr.io`
  était bloqué (uv est donc installé par pip dans l'image).
- `docker compose up -d` : `app` et `grist` démarrent ; l'application joint
  Grist par `http://grist:8484`. Grist demande `GRIST_IN_SERVICE=true` pour
  éviter son assistant `/boot`.
- Le classeur Grist d'origine n'étant pas disponible, un document Grist a été
  construit à partir de `spec/*.csv`, avec des colonnes de référence
  (`profils`, `profil_cible` en `RefList:Profils`, `priorite` numérique,
  colonne `id` sous un autre identifiant interne) :
  - `check-spec --source grist` donne le même résultat qu'en mode `csv` (même
    empreinte `1df30031c3c4`, mêmes constats) ;
  - `spec-export` reproduit les cinq CSV de `spec/` à l'identique.
  À refaire avec le classeur réel après import (README, « Initialisation de
  Grist »).
