"""Reposts de valorisation en échec sur des BL supprimés (clôtures partenaire depuis août 2026).

Voir `customization_app.reposting_bl_supprime` : chaque échec « Delivery Note … not found » est passé en
Skipped et, si le BL supprimé portait des articles de stock, un repost « Item and Warehouse » est posé
par couple article/entrepôt depuis la date du BL (il tourne au prochain passage du planificateur). Seuls les
BL supprimés depuis le 01/08/2026 (flux partenaire) sont re-valorisés ; les orphelins plus anciens (2024-2025)
sont seulement passés en Skipped.
Idempotent : un repost déjà Skipped ou Completed n'est plus candidat.
"""
import frappe

from customization_app.reposting_bl_supprime import reparer_orphelins


def execute():
    resultats = reparer_orphelins()
    if resultats:
        frappe.db.commit()
    return resultats
