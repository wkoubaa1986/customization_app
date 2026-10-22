"""
Commande FERMÉE = statut de livraison « Closed » (demande utilisateur, 22/10/2026).

ERPNext ferme une commande (status = Closed) sans toucher à `delivery_status` : une
commande livrée en partie puis fermée restait « Livré en Partie » et continuait
d'apparaître dans ce filtre. Le champ prévoit pourtant la valeur « Closed » —
ERPNext ne la pose jamais.

La règle : fermée sans être entièrement livrée → « Closed » ; ouverte (ou rouverte)
→ le statut suit per_delivered. Une commande fermée ENTIÈREMENT livrée garde
« Fully Delivered » : c'est l'information juste, il n'y a plus rien à attendre.

Les portes d'ERPNext sont reprises par override_whitelisted_methods — le bouton
Fermer / Rouvrir de la fiche (`update_status`) et l'action groupée de la liste
(`close_or_unclose_sales_orders`) — plutôt que par une surcharge de classe :
woocommerce_fusion surcharge déjà Sales Order. Annuler un BL d'une commande fermée
fait recalculer son statut par ERPNext (update_prevdoc_status) : on le remet.
"""

import json

import frappe
from erpnext.selling.doctype.sales_order import sales_order as _so

FERMEE = "Closed"
LIVREE = "Fully Delivered"


def statut_livraison(status: str, per_delivered, actuel: str) -> str:
    """Le delivery_status que la commande doit porter.

    Ouverte, il suit per_delivered avec le seuil d'ERPNext (status_updater) : une commande
    « Régularisée sur le livré » passe à 100 % par update_child_qty_rate, qui ne recalcule PAS
    delivery_status — SAL-ORD-2026-02915 et 03111 restaient « Livré en Partie » à 100 %.
    « Not Applicable » (rien à livrer) n'est jamais réécrit.
    """
    pct = float(per_delivered or 0)
    complet = pct >= 99.999999
    if status == "Closed":
        return LIVREE if complet else FERMEE
    if actuel == "Not Applicable":
        return actuel
    if complet:
        return LIVREE
    return "Partly Delivered" if pct > 0 else "Not Delivered"


def aligner(noms) -> list:
    """Remet le delivery_status de ces commandes en accord avec leur statut. -> noms modifiés."""
    noms = [n for n in (noms or []) if n]
    if not noms:
        return []
    modifies = []
    for so in frappe.get_all("Sales Order", filters={"name": ["in", noms], "docstatus": 1},
                             fields=["name", "status", "per_delivered", "delivery_status"]):
        voulu = statut_livraison(so.status, so.per_delivered, so.delivery_status)
        if voulu != so.delivery_status:
            frappe.db.set_value("Sales Order", so.name, "delivery_status", voulu,
                                update_modified=False)
            modifies.append(so.name)
    return modifies


@frappe.whitelist()
def update_status(status, name):
    _so.update_status(status, name)
    aligner([name])


@frappe.whitelist(methods=["POST"])
def close_or_unclose_sales_orders(names, status):
    _so.close_or_unclose_sales_orders(names, status)
    aligner(json.loads(names) if isinstance(names, str) else names)


def on_delivery_note_change(doc, method=None):
    aligner({i.against_sales_order for i in doc.get("items") or [] if i.against_sales_order})
