"""Carte « Espèce à verser » (Solde Caisse) et page Caisse espèces : la BIAT compte comme banque (02/11/2026).

La carte de l'espace Comptabilité est `api.get_caisse_solde`, qui lit le Server Script API
« get caisse situation api » (EN BASE) : solde initial + ventes en espèces − achats − dépenses − VERSEMENTS, un
versement étant une écriture qui crédite une caisse (Espèces, Sabah Espèces) et débite un compte de
`BANQUE_ACCOUNTS`. Ce tuple ne contenait que Zitouna : un versement d'espèces à la BIAT ne sortait pas de l'espèce à
verser. Ce patch ajoute « BIAT - A&S » au tuple, dans le script en base (fins de ligne mixtes : on remplace le
tuple seul, sans toucher aux retours à la ligne). Idempotent.
"""
import frappe

SCRIPT = "get caisse situation api"
ANCIEN = 'BANQUE_ACCOUNTS    = ("STE430127B - Zitouna - A&S",)'
NOUVEAU = 'BANQUE_ACCOUNTS    = ("STE430127B - Zitouna - A&S", "BIAT - A&S")   # 02/11/2026 : la BIAT compte comme banque'


def execute():
    if not frappe.db.exists("Server Script", SCRIPT):
        return "script absent"
    s = frappe.db.get_value("Server Script", SCRIPT, "script") or ""
    if NOUVEAU in s:
        return "déjà fait"
    if ANCIEN not in s:
        frappe.log_error(s[:3000], "caisse_especes_biat : tuple BANQUE_ACCOUNTS introuvable")
        return "tuple introuvable : script laissé tel quel"
    frappe.db.set_value("Server Script", SCRIPT, "script", s.replace(ANCIEN, NOUVEAU), update_modified=False)
    frappe.db.commit()
    frappe.cache().delete_value("server_script_map")
    return "modifié"
