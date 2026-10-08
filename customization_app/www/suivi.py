"""Page publique /suivi/<code> : le client suit son intervention (état, arrivée estimée, position du technicien
quand il vient vers lui). Sans connexion ; le code est celui du lien envoyé par SMS. La page ne fait qu'afficher :
les données viennent de flotte_gps_messages.etat_suivi (rafraîchies toutes les 60 s)."""

import frappe


def get_context(context):
    context.no_cache = 1
    context.code = (frappe.form_dict.get("code") or "").strip()
    portail = frappe.db.get_singles_dict("Config Portail RDV") or {}
    context.tel_support = (portail.get("tel_support") or "").strip()
    context.site_web = (portail.get("site_web") or "").strip().rstrip("/")
    return context
