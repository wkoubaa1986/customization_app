"""Rapport « Rapport Espece » (carte « Espèce à verser ») : la BIAT compte comme banque pour les versements (02/11/2026).

Ce rapport est un Script Report vivant EN BASE (pas dans le code). Sa ligne « Espèce déposée à la banque » ne
comptait que les transferts des comptes de liquidités vers Zitouna (`target_account` unique). Ce patch remplace,
dans le script, ce calcul par une boucle sur Zitouna ET « BIAT - A&S » : un versement d'espèces à la BIAT (transfert
interne ou écriture de journal depuis un compte de Liquidités) sort désormais de l'espèce à verser. Le reste du
rapport (facturé espèces, achats, dépenses) est inchangé ; chèques, virements et traites ne sont pas concernés.
Idempotent : ne touche le script que si l'ancien bloc y est encore.
"""
import frappe

RAPPORT = "Rapport Espece"
ANCIEN = '''target_account = "STE430127B - Zitouna - A&S"
payments = get_payment_entries(source_accounts, target_account, start_date, end_date)
journal = get_journal_entries(source_accounts, target_account, start_date, end_date)
versement_espece=0
for ipay in payments:
    versement_espece=versement_espece+ipay['paid_amount']
for ipay in journal:
    versement_espece=versement_espece+ipay['credit']
'''
NOUVEAU = '''# 02/11/2026 : la BIAT compte comme banque — un versement d'espèces vers l'un ou l'autre sort de l'espèce à verser.
target_accounts = ["STE430127B - Zitouna - A&S", "BIAT - A&S"]
versement_espece=0
for target_account in target_accounts:
    for ipay in get_payment_entries(source_accounts, target_account, start_date, end_date):
        versement_espece=versement_espece+ipay['paid_amount']
    for ipay in get_journal_entries(source_accounts, target_account, start_date, end_date):
        versement_espece=versement_espece+ipay['credit']
'''


def execute():
    if not frappe.db.exists("Report", RAPPORT):
        return "rapport absent"
    script = frappe.db.get_value("Report", RAPPORT, "report_script") or ""
    # Le script est enregistré avec des fins de ligne Windows (CRLF) : on compare et on remplace en les respectant.
    fin = "\r\n" if "\r\n" in script else "\n"
    ancien, nouveau = ANCIEN.replace("\n", fin), NOUVEAU.replace("\n", fin)
    if nouveau in script:
        return "déjà fait"
    if ancien not in script:
        frappe.log_error(script[:3000], "rapport_espece_biat : bloc attendu introuvable")
        return "bloc introuvable : rapport laissé tel quel"
    frappe.db.set_value("Report", RAPPORT, "report_script", script.replace(ancien, nouveau), update_modified=False)
    frappe.db.commit()
    return "modifié"
