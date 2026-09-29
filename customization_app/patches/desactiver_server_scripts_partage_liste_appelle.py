"""Éteint les deux Server Scripts que `partage_partenaire` remplace.

« partager liste appelle avec partenaire » (After Insert) et « enlever pa »
(After Submit) posaient et retiraient les DocShare du partenaire avec
`ignore_permissions=True`, ce qui ne couvre pas le contrôle « Partager » propre
à DocShare : toute validation par un non-System Manager échouait. Le hook
Python fait la même chose avec `ignore_share_permission`.

Désactivés, pas supprimés : réversible d'une case à cocher.
"""
import frappe

SCRIPTS = (
    "partager liste appelle avec partenaire",
    "enlever pa",
)


def execute():
    eteints = []
    for nom in SCRIPTS:
        if not frappe.db.exists("Server Script", nom):
            continue
        if frappe.db.get_value("Server Script", nom, "disabled"):
            continue
        frappe.db.set_value("Server Script", nom, "disabled", 1)
        eteints.append(nom)
    if eteints:
        frappe.clear_cache()   # les Server Scripts sont en cache
        frappe.db.commit()
    return eteints
