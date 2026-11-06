# Moteur de règles (référence)

Point de départ pour le développement, en lecture seule. Lit `regles/` à la
racine du dépôt (ou le dossier de la variable d'environnement `REGLES`).

    uv run --with pyyaml --with rule-engine python reference/moteur-regles/moteur.py verifier
    uv run --with pyyaml --with rule-engine python reference/moteur-regles/moteur.py generer these-originale-imprimee reference/moteur-regles/exemple_contexte.json
    uv run --with pyyaml --with rule-engine --with pytest pytest reference/moteur-regles

- `moteur.py` : chargement, décision, génération, vérification (environ 150 lignes).
- `exemple_contexte.json` : contexte d'une thèse fictive.
- `tests/test_moteur.py` : tests sur les règles réelles.
