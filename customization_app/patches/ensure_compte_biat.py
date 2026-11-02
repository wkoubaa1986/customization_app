"""Compte bancaire BIAT et rangement des comptes fermés (demande du 02/11/2026).

1. « BIAT - A&S » : compte de type Banque sous « Comptes bancaires - A&S » (TND ; le n° 08074023011000320548 est sur la fiche
   Bank Account, pas dans le nom du compte — ERPNext le mettrait en préfixe), avec sa
   banque « BIAT » et sa fiche Bank Account de société. Sous ce groupe, il compte d'office comme argent en banque
   partout où le GROUPE fait foi (situation mensuelle, rapprochement client « encaissé en banque », état des comptes)
   et un versement d'espèces s'enregistre dessus (transfert interne Espèces → BIAT). Les chèques, virements, traites
   et la synchronisation bancaire restent sur Zitouna : rien n'est branché sur BIAT pour l'instant.
2. « Ayman Fourati - A&S », « Comptes Epargne Attijari - A&S » et « Revolut - A&S » : désactivés depuis longtemps
   mais porteurs d'écritures, donc INDESTRUCTIBLES (ERPNext refuse). Ils sortent de « Comptes bancaires » vers un
   groupe « Comptes fermés - A&S » (même racine), replié par défaut dans le plan comptable.
3. « Comptes Epargne Attijari - A&S » portait encore un solde créditeur de 496,140 (dernier mouvement 21/09/2024) : il est
   soldé par une écriture de journal au 31/12/2025, débit Attijari / crédit « Depenses personnelle - A&S » (décision
   utilisateur 02/11/2026 : « une transaction de la dettes personnelles à ce compte, espèce »), le compte étant
   réactivé le temps de l'écriture puis désactivé à nouveau. Les trois comptes fermés finissent à zéro.
Idempotent.
"""
import frappe
from frappe.utils import flt

SOCIETE = "Aquaworld & Servicing"
GROUPE_BANQUES = "Comptes bancaires - A&S"
GROUPE_FERMES = "Comptes fermés - A&S"
COMPTE_BIAT = "BIAT - A&S"
NUMERO_BIAT = "08074023011000320548"
A_FERMER = ("Ayman Fourati - A&S", "Comptes Epargne Attijari - A&S", "Revolut - A&S")
COMPTE_ATTIJARI = "Comptes Epargne Attijari - A&S"
COMPTE_PERSONNEL = "Depenses personnelle - A&S"
DATE_SOLDE = "2025-12-31"
REMARQUE_SOLDE = "Clôture du compte Épargne Attijari : solde remis en espèces depuis Dépenses personnelles (02/11/2026)"


def _solde(compte) -> float:
    return flt(frappe.db.sql("select sum(debit) - sum(credit) from `tabGL Entry` where account = %s and is_cancelled = 0",
                             (compte,))[0][0], 3)


def _solder_attijari(fait: list):
    """Le compte fermé finit à zéro : une écriture au 31/12/2025 contre Dépenses personnelles, montant = son solde."""
    if not (frappe.db.exists("Account", COMPTE_ATTIJARI) and frappe.db.exists("Account", COMPTE_PERSONNEL)):
        return
    if frappe.db.exists("Journal Entry", {"user_remark": REMARQUE_SOLDE, "docstatus": 1}):
        return
    solde = _solde(COMPTE_ATTIJARI)
    if abs(solde) < 0.0005:
        return
    montant = abs(solde)
    # solde créditeur (négatif) → on débite Attijari ; débiteur → on le crédite
    attijari = {"account": COMPTE_ATTIJARI, ("debit_in_account_currency" if solde < 0 else "credit_in_account_currency"): montant}
    personnel = {"account": COMPTE_PERSONNEL, ("credit_in_account_currency" if solde < 0 else "debit_in_account_currency"): montant}
    etait_desactive = frappe.db.get_value("Account", COMPTE_ATTIJARI, "disabled")
    if etait_desactive:
        frappe.db.set_value("Account", COMPTE_ATTIJARI, "disabled", 0, update_modified=False)
        frappe.clear_cache(doctype="Account")
    try:
        je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": SOCIETE,
                             "posting_date": DATE_SOLDE, "cheque_no": "espèce", "cheque_date": DATE_SOLDE,
                             "user_remark": REMARQUE_SOLDE, "accounts": [attijari, personnel]})
        je.flags.ignore_permissions = True
        je.insert()
        je.submit()
        fait.append(f"soldé {COMPTE_ATTIJARI} par {je.name} ({montant:.3f})")
    finally:
        if etait_desactive:
            frappe.db.set_value("Account", COMPTE_ATTIJARI, "disabled", 1, update_modified=False)
            frappe.clear_cache(doctype="Account")


def execute():
    fait = []
    if not frappe.db.exists("Account", GROUPE_BANQUES):
        return fait
    racine = frappe.db.get_value("Account", GROUPE_BANQUES, "parent_account")
    # 1. BIAT
    if not frappe.db.exists("Account", COMPTE_BIAT):
        frappe.get_doc({"doctype": "Account", "account_name": "BIAT", "parent_account": GROUPE_BANQUES, "company": SOCIETE,
                        "account_type": "Bank", "account_currency": "TND", "is_group": 0
                        }).insert(ignore_permissions=True)
        fait.append("compte " + COMPTE_BIAT)
    if not frappe.db.exists("Bank", "BIAT"):
        frappe.get_doc({"doctype": "Bank", "bank_name": "BIAT"}).insert(ignore_permissions=True)
        fait.append("banque BIAT")
    if not frappe.db.exists("Bank Account", {"account": COMPTE_BIAT}):
        frappe.get_doc({"doctype": "Bank Account", "account_name": "Compte BIAT", "bank": "BIAT", "account": COMPTE_BIAT,
                        "bank_account_no": NUMERO_BIAT, "is_company_account": 1, "company": SOCIETE, "is_default": 0
                        }).insert(ignore_permissions=True)
        fait.append("fiche Bank Account BIAT")
    # 2. Comptes fermés
    if not frappe.db.exists("Account", GROUPE_FERMES):
        frappe.get_doc({"doctype": "Account", "account_name": "Comptes fermés", "parent_account": racine, "company": SOCIETE,
                        "is_group": 1}).insert(ignore_permissions=True)
        fait.append("groupe " + GROUPE_FERMES)
    for nom in A_FERMER:
        if not frappe.db.exists("Account", nom):
            continue
        maj = {}
        if frappe.db.get_value("Account", nom, "parent_account") != GROUPE_FERMES:
            maj["parent_account"] = GROUPE_FERMES
        if not frappe.db.get_value("Account", nom, "disabled"):
            maj["disabled"] = 1
        if maj:
            doc = frappe.get_doc("Account", nom)
            doc.update(maj)
            doc.flags.ignore_permissions = True
            doc.save()
            fait.append("fermé " + nom)
    # 3. Attijari à zéro, puis contrôle : les comptes fermés ne doivent plus rien porter.
    _solder_attijari(fait)
    for nom in A_FERMER:
        if frappe.db.exists("Account", nom) and abs(_solde(nom)) >= 0.0005:
            frappe.log_error(f"{nom} : solde {_solde(nom)}", "ensure_compte_biat : compte fermé non soldé")
    if fait:
        frappe.db.commit()
    return fait
