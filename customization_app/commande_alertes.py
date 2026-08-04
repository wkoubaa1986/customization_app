"""
Anomalies signalées dans la liste des commandes client.

Trois situations invisibles à l'œil nu sur une liste de près de 10 000
commandes, remontées ici pour colorer la ligne :

  rouge   une ligne de main d'œuvre, mais aucune intervention planifiée
  rouge   une ligne de livraison, mais aucune intervention planifiée
  orange  une tâche terminée, alors que la commande n'est ni validée ni soldée

Les règles sont mutuellement exclusives : les deux premières exigent l'absence
de toute tâche non annulée, la troisième exige une tâche terminée.

Consommé par public/js/sales_order_list_alertes.js.
"""

import json

import frappe

from customization_app.per_delivered_montant import MARGE

GROUPE_MAIN_OEUVRE = "Main d’œuvre"  # apostrophe typographique U+2019
GROUPE_LIVRAISON = "Livraison"

# Une page de liste Frappe affiche au plus 100 lignes.
MAX_NOMS = 100

ROUGE_MAIN_OEUVRE = ("rouge", "Main d'œuvre sans tâche")
ROUGE_LIVRAISON = ("rouge", "Livraison sans tâche")
ORANGE_NON_SOLDEE = ("orange", "Tâche terminée, commande non soldée")


@frappe.whitelist()
def get_alertes(noms):
    """
    Retourne {nom_commande: {"couleur": ..., "libelle": ...}} pour les seules
    commandes en anomalie. Les commandes saines sont absentes du résultat.
    """
    if isinstance(noms, str):
        noms = json.loads(noms)
    noms = [n for n in (noms or []) if n][:MAX_NOMS]
    if not noms:
        return {}

    lignes = frappe.db.sql(
        """
        SELECT
            so.name,
            (SELECT COUNT(*) FROM `tabTache de travail` t
             WHERE t.commande_client = so.name AND t.status <> 'Cancelled') AS taches_actives,
            (SELECT COUNT(*) FROM `tabTache de travail` t
             WHERE t.commande_client = so.name AND t.status = 'Completed') AS taches_terminees,
            EXISTS (SELECT 1 FROM `tabSales Order Item` si
                    JOIN `tabItem` i ON i.name = si.item_code
                    WHERE si.parent = so.name AND i.item_group = %(main_oeuvre)s) AS a_main_oeuvre,
            EXISTS (SELECT 1 FROM `tabSales Order Item` si
                    JOIN `tabItem` i ON i.name = si.item_code
                    WHERE si.parent = so.name AND i.item_group = %(livraison)s) AS a_livraison,
            so.docstatus,
            so.grand_total,
            COALESCE((SELECT SUM(dn.grand_total) FROM `tabDelivery Note` dn
                      WHERE dn.docstatus = 1 AND EXISTS (
                        SELECT 1 FROM `tabDelivery Note Item` dni
                        WHERE dni.parent = dn.name AND dni.against_sales_order = so.name)), 0) AS total_bl
        FROM `tabSales Order` so
        WHERE so.name IN %(noms)s AND so.docstatus < 2
    """,
        {
            "noms": tuple(noms),
            "main_oeuvre": GROUPE_MAIN_OEUVRE,
            "livraison": GROUPE_LIVRAISON,
        },
        as_dict=True,
    )

    alertes = {}
    for r in lignes:
        motif = _motif(r)
        if motif:
            alertes[r.name] = {"couleur": motif[0], "libelle": motif[1]}
    return alertes


def _motif(r):
    """Premier motif d'anomalie d'une commande, ou None si elle est saine."""
    if not r.taches_actives:
        if r.a_main_oeuvre:
            return ROUGE_MAIN_OEUVRE
        if r.a_livraison:
            return ROUGE_LIVRAISON
        return None

    # Même définition de « soldé » que per_delivered_montant.aligner_commande :
    # les deux ne doivent pas diverger, sans quoi une commande pourrait être à
    # 100 % livré et signalée orange en même temps.
    if r.taches_terminees and (r.docstatus == 0 or r.total_bl < r.grand_total - MARGE):
        return ORANGE_NON_SOLDEE

    return None
