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
Idempotent.
"""
import frappe

SOCIETE = "Aquaworld & Servicing"
GROUPE_BANQUES = "Comptes bancaires - A&S"
GROUPE_FERMES = "Comptes fermés - A&S"
COMPTE_BIAT = "BIAT - A&S"
NUMERO_BIAT = "08074023011000320548"
A_FERMER = ("Ayman Fourati - A&S", "Comptes Epargne Attijari - A&S", "Revolut - A&S")


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
    if fait:
        frappe.db.commit()
    return fait
