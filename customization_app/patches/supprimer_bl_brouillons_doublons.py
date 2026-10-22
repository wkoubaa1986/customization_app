"""
Supprime les BL de livraison restés en BROUILLON sur des commandes déjà livrées par leurs BL validés
(demande utilisateur, 22/10/2026).

Cas réel : SAL-ORD-2026-02990 — MAT-DN-2026-03549 (354, brouillon « En attente validation magasin »
du 15/08) à côté de MAT-DN-2026-03584 (354, validé le 16/08). Le brouillon ne livrera jamais rien :
il affichait « BL à valider » et risquait d'être validé une seconde fois (double sortie de stock).

Un brouillon n'est supprimé que si TOUTES ses lignes visent des commandes couvertes : 100 % livrées
ET dont les BL validés atteignent le TTC (à MARGE près, cf. per_delivered_montant). Jamais un BL de
retour, jamais un BL dont une ligne n'est rattachée à aucune commande. Un brouillon qui COMPLÈTE une
livraison partielle (SAL-ORD-2026-03983, 6 cartouches) n'est donc pas touché. Idempotent.
"""

import frappe
from frappe.utils import flt

from customization_app.per_delivered_montant import MARGE, total_bl_valides


def _couverte(nom: str) -> bool:
    so = frappe.db.get_value("Sales Order", nom, ["docstatus", "per_delivered", "grand_total"], as_dict=True)
    return bool(so and so.docstatus == 1 and flt(so.per_delivered) >= 99.999999
                and total_bl_valides(nom) >= flt(so.grand_total) - MARGE)


def execute():
    brouillons = frappe.db.sql_list("""
        SELECT DISTINCT dn.name FROM `tabDelivery Note` dn
        JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        WHERE dn.docstatus = 0 AND dn.is_return = 0 AND IFNULL(dni.against_sales_order, '') <> ''""")

    supprimes = []
    for nom in brouillons:
        lignes = frappe.get_all("Delivery Note Item", filters={"parent": nom}, pluck="against_sales_order")
        commandes = set(lignes)
        if not lignes or "" in commandes or None in commandes:
            continue
        if not all(_couverte(so) for so in commandes):
            continue
        valides = frappe.db.sql_list("""
            SELECT DISTINCT dn.name FROM `tabDelivery Note` dn
            JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
            WHERE dn.docstatus = 1 AND dn.is_return = 0 AND dni.against_sales_order IN %(so)s""",
            {"so": tuple(commandes)})
        try:
            frappe.delete_doc("Delivery Note", nom, ignore_permissions=True)
        except Exception:
            frappe.log_error(title="supprimer_bl_brouillons_doublons — %s" % nom,
                             message=frappe.get_traceback())
            continue
        for so in commandes:
            frappe.get_doc("Sales Order", so).add_comment(
                "Comment", "🧹 BL brouillon %s supprimé (22/10/2026) : doublon, la commande est déjà "
                           "livrée par %s." % (nom, ", ".join(valides) or "ses BL validés"))
        supprimes.append("%s (%s)" % (nom, ", ".join(sorted(commandes))))

    print("[supprimer_bl_brouillons_doublons] %d brouillon(s) supprimé(s) : %s"
          % (len(supprimes), ", ".join(supprimes)))
