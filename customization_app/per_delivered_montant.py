"""
Pourcentage livré des commandes client aligné sur les montants, pas sur les
quantités.

Problème résolu
---------------
ERPNext calcule `per_delivered` comme somme(delivered_qty) / somme(qty) sur les
lignes de la commande, jointes au BL par `against_sales_order`. Lors d'un
échange d'article, le BL porte l'article réellement remis — différent du
placeholder « Echange » de la commande — et cette ligne n'est rattachée à
aucune ligne de commande. Sa quantité n'est donc jamais comptée : la commande
reste indéfiniment à 50 % ou 66,7 % alors qu'elle est intégralement livrée.

Règle appliquée
---------------
Si la somme TTC des bons de livraison **validés** rattachés à la commande
couvre son propre TTC, la commande est livrée à 100 %. Le statut est ensuite
laissé au calcul standard d'ERPNext (« À facturer » ou « Terminé » selon la
facturation).

Vérifié sur la base : aucun BL ne sert plusieurs commandes, la somme par
commande est donc sans double comptage ; 24 commandes sont servies par
plusieurs BL, d'où la somme sur l'ensemble des BL.
"""

import frappe
from frappe.utils import flt

# Tolérance d'arrondi entre le TTC d'une commande et celui de ses BL.
#
# Distribution mesurée sur les commandes validées ayant au moins un BL validé :
#   <= 0,01 DT   9484   écart d'arrondi pur (ex. 39.999 contre 40.000)
#   0,01 à 0,10     4   arrondi de TVA (ex. 625.922 contre 625.992)
#   0,10 à 1,00    11   \
#   1 à 10 DT      11    > écarts réels, à signaler
#   > 10 DT        29   /
#
# La coupure naturelle est à 0,10 : en deçà il n'existe que des artefacts
# d'arrondi, au-delà les manques sont réels.
MARGE = 0.10


def _commandes_liees(doc):
    """Commandes client rattachées à un BL, par ses lignes ou par custom_commande."""
    noms = {
        ligne.against_sales_order
        for ligne in (doc.get("items") or [])
        if ligne.get("against_sales_order")
    }
    if doc.get("custom_commande"):
        noms.add(doc.custom_commande)
    return {n for n in noms if n}


def total_bl_valides(nom_commande):
    """Somme des TTC des bons de livraison validés rattachés à cette commande."""
    return flt(
        frappe.db.sql(
            """
            SELECT SUM(dn.grand_total)
            FROM `tabDelivery Note` dn
            WHERE dn.docstatus = 1
              AND EXISTS (
                SELECT 1 FROM `tabDelivery Note Item` dni
                WHERE dni.parent = dn.name AND dni.against_sales_order = %s)
        """,
            nom_commande,
        )[0][0]
    )


def aligner_commande(nom_commande):
    """
    Passe la commande à 100 % livré si ses BL validés couvrent son TTC.

    Ne fait rien dans le cas contraire : la valeur calculée par ERPNext reste
    en place, y compris après l'annulation d'un BL, où ERPNext l'a déjà
    recalculée avant notre passage.

    Retourne True si la commande a été modifiée.
    """
    commande = frappe.db.get_value(
        "Sales Order",
        nom_commande,
        ["name", "docstatus", "status", "grand_total", "per_delivered"],
        as_dict=True,
    )
    if not commande or commande.docstatus != 1:
        return False
    if commande.status in ("Closed", "Cancelled"):
        return False

    # Une commande à zéro n'a rien à livrer : sans ce garde-fou, une somme de
    # BL nulle « couvrirait » son total et la passerait à 100 %.
    if flt(commande.grand_total) <= 0:
        return False
    if flt(commande.per_delivered) >= 100:
        return False

    if total_bl_valides(nom_commande) < flt(commande.grand_total) - MARGE:
        return False

    doc = frappe.get_doc("Sales Order", nom_commande)
    doc.db_set("per_delivered", 100, update_modified=False)
    doc.set_status(update=True, update_modified=False)
    return True


def on_delivery_note_change(doc, method=None):
    """
    Réévalue les commandes du BL après validation ou annulation.

    Appelé après update_prevdoc_status d'ERPNext, donc notre valeur prime sur
    le pourcentage calculé sur les quantités.
    """
    for nom in _commandes_liees(doc):
        try:
            aligner_commande(nom)
        except Exception:
            frappe.log_error(
                title=f"per_delivered montant — {nom}", message=frappe.get_traceback()
            )
