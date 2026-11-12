#!/usr/bin/env bash
# Sauvegarde nocturne des règles et des prompts modifiés depuis l'application
# (studio, page Prompts) : commit puis push, à lancer par cron sur l'hôte du VPS.
#
#   sauvegarde-regles.sh [chemin du clone]     (défaut : dossier parent du script)
#
# Variables facultatives :
#   BRANCHE   branche distante qui reçoit les commits (défaut : regles-vps),
#             à fusionner dans main par une PR (fusion classique, sans squash)
#   DISTANT   dépôt distant (défaut : origin)
#   AUTEUR    auteur des commits (défaut : « Studio Humathèque (VPS) <vps@localhost> »)
#
# Ne versionne que regles/ et config/prompts/ (les archives sont ignorées par git) ;
# rien d'autre n'est ajouté ni commité. Sans modification : rien ne se passe.
# L'accès en écriture au dépôt est celui du compte qui lance le script (clé de
# déploiement SSH conseillée) : aucun secret dans ce fichier.
set -euo pipefail

DEPOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
BRANCHE="${BRANCHE:-regles-vps}"
DISTANT="${DISTANT:-origin}"
AUTEUR="${AUTEUR:-Studio Humathèque (VPS) <vps@localhost>}"
CHEMINS=(regles config/prompts)

cd "$DEPOT"
# Un seul passage à la fois (cron + lancement manuel)
exec 9>"${TMPDIR:-/tmp}/humatheque-sauvegarde-regles.lock"
flock -n 9 || { echo "$(date -Is) déjà en cours"; exit 0; }

git add -A -- "${CHEMINS[@]}"
if git diff --cached --quiet -- "${CHEMINS[@]}"; then
  echo "$(date -Is) aucune modification"
else
  fichiers=$(git diff --cached --name-only -- "${CHEMINS[@]}")
  git -c user.name="${AUTEUR% <*}" -c user.email="$(sed -E 's/.*<(.*)>.*/\1/' <<<"$AUTEUR")" \
    commit --quiet -m "Règles et prompts modifiés depuis l'application ($(date +%F))" \
    -m "$fichiers" -- "${CHEMINS[@]}"
  echo "$(date -Is) commit $(git rev-parse --short HEAD) :"; sed 's/^/  /' <<<"$fichiers"
fi

# Pousse aussi les commits locaux restés en attente (push précédent en échec)
if git push --quiet "$DISTANT" "HEAD:refs/heads/$BRANCHE"; then
  echo "$(date -Is) poussé vers $DISTANT/$BRANCHE"
else
  echo "$(date -Is) ÉCHEC du push vers $DISTANT/$BRANCHE : le commit reste local, à pousser à la main" >&2
  exit 1
fi
