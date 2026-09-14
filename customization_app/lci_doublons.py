"""Liste Commande Import : enlever les doublons, c'est-à-dire regrouper les lignes qui portent
deux fois le même article en une seule ligne dont la quantité est la SOMME.

CE QUE CE FICHIER DÉCIDE
------------------------
Une liste d'import se construit au fil des jours : on ajoute un article, puis on l'ajoute encore
trois semaines plus tard sans voir qu'il y est déjà. Le fournisseur, lui, lit une demande de
cotation : deux lignes du même article sont pour lui une seule ligne de la quantité totale.

⚠️ LE REGROUPEMENT SE DÉCIDE SUR L'ARTICLE, SON UNITÉ ET SES ADDITIONNELS — PAS SUR L'ARTICLE
SEUL. Deux lignes du même article, l'une en « Pièce » et l'autre en « Carton », ne sont pas un
doublon : les sommer inventerait une quantité qui n'existe dans aucune unité. De même, une ligne
pack qui embarque des articles additionnels (JSON `articles_additionnels`) n'est pas la même ligne
que le pack nu. Ces lignes restent telles quelles, et `non_fusionnees` les signale pour que
l'écran le dise au lieu de se taire.

⚠️ UNE LIGNE LIBRE (SANS CODE ARTICLE) SE RECONNAÎT À SA DÉSIGNATION. Sur la liste d'import, une
ligne « Article libre » n'a pas de code : deux lignes libres à la même désignation (espaces et
casse ignorés) sont le même article. Une ligne sans code ni désignation n'a pas d'identité : on
n'y touche pas.

⚠️ LA PREMIÈRE OCCURRENCE GAGNE, ET C'EST UNE PERTE D'INFORMATION ASSUMÉE. Seule la quantité est
sommée ; l'image, la description, la traduction et la position retenues sont celles de la
première ligne. C'est pourquoi la fusion ne s'applique qu'au clic d'un humain, et jamais toute
seule à l'enregistrement.

Fonctions PURES : aucune base, aucun réseau. L'exposition au formulaire est dans
`liste_commande_import.fusionner_lignes`, qui n'écrit rien : l'écran applique le résultat,
l'utilisateur enregistre.
"""
from __future__ import annotations

import json

#: Les quantités se somment au millième : additionner des flottants sans arrondir fait apparaître
#: des 2.9999999999999996 dans la case de l'utilisateur.
PRECISION_QTY = 3


def _qty(ligne) -> float:
    return round(float(ligne.get("qty") or 0), PRECISION_QTY)


def _additionnels(ligne) -> str:
    """Forme canonique des articles additionnels d'une ligne pack : même contenu → même chaîne."""
    brut = ligne.get("articles_additionnels") or ""
    if not str(brut).strip():
        return ""
    try:
        data = json.loads(brut) if isinstance(brut, str) else brut
    except ValueError:
        return str(brut).strip()
    if not data:
        return ""
    cles = sorted(
        (str(a.get("item_code") or ""), round(float(a.get("qty_par_pack") or 0), PRECISION_QTY))
        for a in data if isinstance(a, dict)
    )
    return json.dumps(cles)


def _identite(ligne) -> str | None:
    """Le code article, sinon la désignation normalisée ; None si la ligne n'a ni l'un ni l'autre."""
    code = (ligne.get("item_code") or "").strip()
    if code:
        return code
    nom = " ".join((ligne.get("item_name") or "").split()).casefold()
    return f"libre:{nom}" if nom else None


def _cle(ligne):
    """Ce qui fait que deux lignes sont LA MÊME ligne. None si la ligne ne se regroupe pas."""
    identite = _identite(ligne)
    if identite is None:
        return None
    return (identite, (ligne.get("uom") or "").strip(), _additionnels(ligne))


def _conservee(ligne) -> dict:
    return {"name": ligne.get("name"), "item_code": ligne.get("item_code"),
            "item_name": ligne.get("item_name"), "qty": _qty(ligne), "fusionnees": 1}


def _libelle(identite: str) -> str:
    return identite[len("libre:"):] if identite.startswith("libre:") else identite


def _non_fusionnees(groupes) -> list:
    """Les articles restés sur plusieurs lignes, et pourquoi. -> [{article, lignes, motif}].

    ⚠️ CE N'EST PAS UN AVERTISSEMENT DE CONFORT. L'utilisateur clique pour qu'il ne reste qu'une
    ligne par article ; s'il en reste deux, il doit savoir que ce n'est pas un oubli du bouton mais
    un désaccord d'unité ou d'additionnels entre ses propres lignes.
    """
    par_article = {}
    for identite, uom, adds in groupes:
        par_article.setdefault(identite, []).append((uom, adds))
    signales = []
    for identite, cles in par_article.items():
        if len(cles) < 2:
            continue
        unites = len({u for u, _ in cles}) > 1
        additionnels = len({a for _, a in cles}) > 1
        signales.append({
            "article": _libelle(identite),
            "lignes": len(cles),
            "motif": ("unité et additionnels" if unites and additionnels
                      else "unité" if unites else "additionnels"),
        })
    return signales


def regrouper(lignes) -> dict:
    """Regroupe les lignes du même article en une seule, en sommant les quantités. Fonction PURE.

    Chaque ligne attendue porte au moins `name`, `item_code`, `item_name`, `uom`, `qty` et,
    pour les packs, `articles_additionnels`.

    -> {"conserver": [{name, item_code, item_name, qty, fusionnees}],  # TOUTES les lignes qui
                                                                      # restent, dans l'ordre
        "supprimer": [name],                    # les lignes en trop
        "doublons": int,                        # combien de lignes disparaissent
        "non_fusionnees": [{article, lignes, motif}]}

    La ligne conservée est la PREMIÈRE de son groupe : elle garde sa place, son image, sa
    description et sa traduction. `fusionnees` dit combien de lignes d'origine elle représente —
    à 1, sa quantité n'a pas bougé et l'écran n'a rien à y toucher.
    """
    groupes = {}
    conserver = []
    supprimer = []
    for ligne in lignes or []:
        cle = _cle(ligne)
        if cle is None:
            conserver.append(_conservee(ligne))
            continue
        gardee = groupes.get(cle)
        if gardee is None:
            gardee = _conservee(ligne)
            groupes[cle] = gardee
            conserver.append(gardee)
            continue
        gardee["qty"] = round(gardee["qty"] + _qty(ligne), PRECISION_QTY)
        gardee["fusionnees"] += 1
        supprimer.append(ligne.get("name"))
    return {"conserver": conserver, "supprimer": supprimer, "doublons": len(supprimer),
            "non_fusionnees": _non_fusionnees(groupes)}
