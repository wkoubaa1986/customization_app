"""
Méthodes exposées au Jinja des print formats (hook « jinja » de hooks.py).
"""

import frappe

CHAMP_GARANTIE = "custom_sous_garantie"


def _codes_articles(doc):
    """Codes articles d'un BL, lignes normales et composants de Product Bundle."""
    codes = set()
    for champ in ("items", "packed_items"):
        lignes = doc.get(champ) if hasattr(doc, "get") else None
        for ligne in lignes or []:
            code = ligne.get("item_code") if hasattr(ligne, "get") else getattr(ligne, "item_code", None)
            if code:
                codes.add(code)
    return codes


def bl_sous_garantie(doc):
    """
    Vrai si au moins une ligne du bon de livraison relève d'un groupe d'articles
    marqué « Sous garantie », ou d'un descendant d'un tel groupe.

    La descendance est résolue par le nested set (lft/rgt) : cocher un groupe
    parent couvre tous ses sous-groupes, ce qui est indispensable pour
    « Pompes de surface & puits » et « Pompes multicellulaires », qui ne
    portent aucun article en direct.

    Utilisé par le print format « Aqua World BL ».
    """
    if not frappe.db.has_column("Item Group", CHAMP_GARANTIE):
        return False

    codes = _codes_articles(doc)
    if not codes:
        return False

    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM `tabItem` i
            JOIN `tabItem Group` g ON g.name = i.item_group
            JOIN `tabItem Group` p ON g.lft >= p.lft AND g.rgt <= p.rgt
            WHERE i.name IN %(codes)s
              AND p.custom_sous_garantie = 1
            LIMIT 1
        """,
            {"codes": tuple(codes)},
        )
    )
