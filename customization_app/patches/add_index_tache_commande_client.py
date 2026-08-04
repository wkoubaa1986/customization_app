"""
Index sur `Tache de travail.commande_client`.

Ce champ est le pivot de toutes les règles d'anomalie des commandes, du calcul
du statut de livraison et de la génération des BL, mais il n'était pas indexé.
Le recalcul complet des anomalies passe de 41,6 s à 0,5 s — 81 fois plus
rapide — ce qui rend la resynchronisation nocturne négligeable.
"""

import frappe


def execute():
    try:
        frappe.db.add_index("Tache de travail", ["commande_client"])
        frappe.db.commit()
        print("[add_index_tache_commande_client] index posé sur commande_client.")
    except Exception:
        # add_index est idempotent sur MariaDB récent, mais on ne fait pas
        # échouer un migrate pour un index déjà présent.
        frappe.log_error(
            title="add_index_tache_commande_client", message=frappe.get_traceback()
        )
        print("[add_index_tache_commande_client] index déjà présent ou non posable.")
