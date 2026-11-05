"""Titre de classement : `@` devant le premier mot significatif (SPEC §4.4).

Le titre est déjà en casse de phrase. Articles par langue en configuration ;
élision sans espace (`L'@évolution`), autres articles suivis d'une espace
(`Les @coraux`).
"""

from __future__ import annotations

from humatheque.texte_unimarc.resultat import Resultat
from humatheque.texte_unimarc.ressources import configuration


def titre_classement(titre: str, langue: str | None) -> Resultat:
    titre = (titre or "").strip()
    if not titre:
        return Resultat(valeur="")
    articles = configuration()["articles"].get((langue or "").strip().lower())
    if articles is None:
        return Resultat(valeur=titre, alertes=[f"langue_sans_table_articles:{langue or 'absente'}"],
                        trace=[f"langue « {langue} » sans table d'articles : pas de @"])
    if titre.startswith("@") or " @" in titre or "'@" in titre:
        return Resultat(valeur=titre, trace=["@ déjà présent"])

    minuscule = titre.lower().replace("’", "'")
    for article in sorted(articles, key=len, reverse=True):
        a = article.lower()
        if a.endswith("'"):
            if minuscule.startswith(a) and len(titre) > len(a) and titre[len(a)].isalpha():
                n = len(a)
                return Resultat(valeur=f"{titre[:n]}@{titre[n:]}", trace=[f"article élidé « {titre[:n]} »"])
        else:
            premier, _, reste = titre.partition(" ")
            if premier.lower() == a and reste.strip():
                return Resultat(valeur=f"{premier} @{reste.lstrip()}", trace=[f"article « {premier} »"])
    return Resultat(valeur=titre, trace=["pas d'article initial"])
