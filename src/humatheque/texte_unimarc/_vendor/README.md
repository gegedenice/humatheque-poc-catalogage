# Code tiers vendorisé

## reaccentue

- Origine : https://github.com/cquest/reaccentue, commit
  `bc48cfd12c9ad74da625700180d6a2f3c807cac2` (12/02/2021).
- **Le dépôt n'a pas de fichier LICENSE** : aucune licence n'est déclarée
  pour le code. Le dictionnaire `fr-toutesvariantes` provient de Dicollecte
  (Grammalecte, publié sous MPL 2.0 selon le projet d'origine) ; `freq5.pz`
  (fréquences de bigrammes) est calculé par l'auteur sur un dump de Wikipédia
  en français (CC BY-SA). À faire valider avant toute diffusion.
- Fichiers :
  - `reaccentue/reaccentue_origine.py` : copie **non modifiée** du script
    d'origine, conservée pour référence ; elle n'est jamais importée ;
  - `reaccentue/dictionnaire.py` : reprise des règles de construction du
    dictionnaire (`add_dico`, `load_word`, `load_dico`, `reduce_dico`) avec
    chemins relatifs au module, cache dans `data/reaccentue-cache.pz`
    (construit par `humatheque cache-reaccentue`, au build Docker), relevé
    des noms propres avant réduction ; pas d'exécution à l'import ;
  - `reaccentue/dico/` : `fr-toutesvariantes.dic/.aff`, `complements.dic`,
    `freq5.pz` (≈ 25 Mo), copies non modifiées.
- La fonction `reaccentue()` d'origine n'est pas utilisée (SPEC §4.1) : la
  tokenisation et le choix des formes sont dans `texte_unimarc/casse.py`.
