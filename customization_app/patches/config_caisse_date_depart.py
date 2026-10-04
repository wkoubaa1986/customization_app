"""Config Caisse : date de départ de la double validation = 05/10/2026 (décision utilisateur 04/10/2026,
« considérer qu'à partir de cette date toutes les caisses sont vides ») ; Wassim ajouté aux co-responsables de collecte
s'il n'y est pas. Idempotent.

⚠️ Pas `get_single_value` pour tester le vide : une Date NULL y ressort en 0001-01-01 (truthy) et le patch
ne posait jamais la date. On lit tabSingles directement (pas de colonne modified : SQL brut)."""
import frappe


def execute():
    frappe.reload_doc("customize_erpnext", "doctype", "config_caisse")
    actuelle = frappe.db.sql(
        "SELECT value FROM tabSingles WHERE doctype = 'Config Caisse' AND field = 'date_depart'")
    if not (actuelle and actuelle[0][0]):
        frappe.db.set_single_value("Config Caisse", "date_depart", "2026-10-05")
    # Wassim co-responsable de collecte (demande 04/10/2026) : même rang que le titulaire, sans passation.
    frappe.reload_doc("customize_erpnext", "doctype", "config_caisse_delegue")
    wassim = "koubaawassim@gmail.com"
    if frappe.db.exists("User", wassim):
        cfg = frappe.get_single("Config Caisse")
        deja = {r.user for r in (cfg.get("responsables") or [])} | {cfg.get("responsable")}
        if wassim not in deja:
            cfg.append("responsables", {"user": wassim})
            cfg.save(ignore_permissions=True)
    frappe.clear_cache(doctype="Config Caisse")
