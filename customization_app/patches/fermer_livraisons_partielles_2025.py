"""
Ferme les commandes livrées EN PARTIE datées de 2025 ou avant (décision utilisateur, 22/10/2026).

Analyse sur la prod du 22/10/2026 : 14 commandes, toutes de 2023-2024, en trois cas —
  - tout livré, mais BL saisis moins cher que la commande (10, mars 2024) : le % livré,
    calculé au montant, ne pouvait jamais atteindre 100 ;
  - un retour jamais compensé (Nizar Maddouri, Sté SOTELMOD) ;
  - un reste jamais livré (Lycée Pierre Mendès France, Ambassade américaine).
Rien n'est plus attendu sur aucune : elles sont fermées, avec un commentaire qui dit pourquoi.

Puis TOUTES les commandes fermées sans être entièrement livrées passent au statut de livraison
« Closed » (cf. fermeture_commande) : elles quittent le filtre « Livré en Partie » / « Non livré ».

Idempotent : une commande déjà fermée n'est plus candidate ; l'alignement ne réécrit que l'écart.
"""

import frappe

from customization_app.fermeture_commande import aligner

DATE_MAX = "2025-12-31"


def _cas(nom: str) -> str:
    lignes = frappe.get_all("Sales Order Item", filters={"parent": nom},
                            fields=["qty", "delivered_qty", "returned_qty"])
    if any(float(l.returned_qty or 0) > 0 for l in lignes):
        return "retour d'articles jamais compensé"
    if all(float(l.delivered_qty or 0) >= float(l.qty or 0) for l in lignes):
        return "tout livré, BL saisis à un prix inférieur à la commande"
    return "reste jamais livré"


def execute():
    candidates = frappe.get_all("Sales Order", filters=[
        ["docstatus", "=", 1],
        ["per_delivered", ">", 0],
        ["per_delivered", "<", 100],
        ["status", "not in", ["Closed", "Completed", "Cancelled"]],
        ["transaction_date", "<=", DATE_MAX],
    ], pluck="name")

    fermees = []
    for nom in candidates:
        try:
            cas = _cas(nom)
            so = frappe.get_doc("Sales Order", nom)
            so.update_status("Closed")
            so.add_comment("Comment", "🧹 Fermée automatiquement (22/10/2026) : livrée en partie, "
                                      "commande de 2025 ou avant — %s." % cas)
            fermees.append(nom)
        except Exception:
            frappe.log_error(title="fermer_livraisons_partielles_2025 — %s" % nom,
                             message=frappe.get_traceback())

    alignees = aligner(frappe.get_all("Sales Order", filters={"docstatus": 1, "status": "Closed"},
                                      pluck="name"))

    if fermees:
        try:
            from customization_app.commande_alertes import recalculer
            recalculer(fermees)
        except Exception:
            frappe.log_error(title="fermer_livraisons_partielles_2025 — anomalies",
                             message=frappe.get_traceback())

    print("[fermer_livraisons_partielles_2025] %d commande(s) fermée(s), %d statut(s) de livraison "
          "passés à « Closed »" % (len(fermees), len(alignees)))
