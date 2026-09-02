"""Éteint les deux Server Scripts que `annulation_facture` remplace.

Sans cela ils travailleraient EN DOUBLE avec le nouveau code : l'un chercherait
encore la commande dans le libellé du paiement, l'autre supprimerait le
Payment Entry que le nouveau vient d'amender. Deux mécanismes qui se
contredisent sur des écritures comptables, c'est le pire des deux.

Ils sont DÉSACTIVÉS, pas supprimés : leur code reste consultable, et le geste
est réversible d'une case à cocher si quelque chose devait manquer.

Même précédent que « Generation N Facture », éteint quand
`facturation_numbering` a repris la numérotation.
"""
import frappe

SCRIPTS = (
    "cancel Invoice order Payment",
    "traitement paiement après annulation facture",
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
        # Les Server Scripts sont mis en cache : sans ce vidage, l'ancien
        # continuerait de tourner jusqu'au prochain redémarrage.
        frappe.clear_cache()
        frappe.db.commit()
    return eteints
