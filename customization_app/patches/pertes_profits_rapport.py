"""Le Script Report « Benéfice société » (graphique Pertes et Profits de l'onglet Comptabilité) délègue son calcul à
customization_app.pertes_profits (05/11/2026) — mêmes chiffres, plus les omissions « Pertes et Profits ».

Le script d'origine reste lisible dans l'historique du rapport (Version : le save est suivi). Idempotent. Les
omissions d'avant le champ `portee` sont marquées « Situation mensuelle » (ce qu'elles étaient)."""
import frappe

from customization_app.pertes_profits import RAPPORT, SCRIPT_RAPPORT
from customization_app.situation_mensuelle import DOCTYPE_EXCLUSION, PORTEE_SITUATION


def execute():
    if frappe.db.has_column(DOCTYPE_EXCLUSION, "portee"):
        frappe.db.sql("update `tab%s` set portee = %%s where ifnull(portee, '') = ''" % DOCTYPE_EXCLUSION, PORTEE_SITUATION)
    if not frappe.db.exists("Report", RAPPORT):
        return
    rapport = frappe.get_doc("Report", RAPPORT)
    if (rapport.report_script or "").strip() == SCRIPT_RAPPORT.strip():
        return
    rapport.report_script = SCRIPT_RAPPORT
    rapport.flags.ignore_permissions = True
    rapport.save()
