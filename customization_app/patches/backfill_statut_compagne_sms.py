"""Campagnes SMS historiques (avant l'assistant, 03/10/2026) : soumises mais sans statut → « Envoyée », segments repris
de nombre_total_compagne. Idempotent."""
import frappe


def execute():
    if not frappe.db.has_column("Compagne SMS", "statut"):
        return
    frappe.db.sql("""update `tabCompagne SMS` set statut = 'Envoyée', envoye_le = ifnull(envoye_le, modified),
                     envoyes = ifnull(nullif(envoyes, 0), nombre_total_compagne)
                     where docstatus = 1 and ifnull(statut, '') in ('', 'Brouillon')""")
    frappe.db.sql("""update `tabCompagne SMS` set statut = 'Brouillon' where docstatus = 0 and ifnull(statut, '') = ''""")
