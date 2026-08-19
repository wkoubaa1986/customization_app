"""
Dépenses saisies depuis la caisse journalière.

TROIS TYPES (décision utilisateur 19/08) :
  - « Dépense non facturée »  : saisie directe, aucune pièce exigée ;
  - « Dépense avec facture »  : la PHOTO de la facture est OBLIGATOIRE ;
  - « Facture d'achat »       : photo obligatoire aussi — la dépense est marquée
                                « à intégrer au flux achat » (la facture formelle,
                                avec sa TVA récupérable, suit le pipeline achat —
                                phase ultérieure).

La photo peut être ANALYSÉE par OpenAI (extract_invoice_image de
bank_retenue_sync — prompt scan tunisien) pour préremplir fournisseur, montant
et description : l'employé confirme, jamais l'inverse.

MODES DE PAIEMENT ET COMPTES :
  - Espèces         -> Cr « Espèces - A&S » (la caisse commune de l'équipe) ;
  - Chèque          -> Cr Zitouna, n° à 7 CHIFFRES + banque + PHOTO du chèque
                       obligatoires ; le n° est cité dans la remarque — c'est lui
                       que l'identification bancaire lit quand le débit
                       « REGLEMENT CHEQUE nnnnnnn » paraît au relevé ;
  - Carte de crédit -> Cr Zitouna, remarque « Réglé par carte bancaire » (les
                       débits « REGLEMENT CB » se rapprochent par montant+date).

L'écriture est SOUMISE (une dépense de caisse est un fait), les photos sont
attachées au document.
"""

import base64
import re

import frappe
from frappe import _
from frappe.utils import flt, nowdate

COMPTE_ESPECES = "Espèces - A&S"
COMPTE_BANQUE = "STE430127B - Zitouna - A&S"
COMPTE_DEPENSE_DEFAUT = "Dépenses non déclarées - A&S"
COMPANY = "Aquaworld & Servicing"
CC = "Principal - A&S"

TYPES = ("Dépense non facturée", "Dépense avec facture", "Facture d'achat")
MODES = ("Espèces", "Chèque", "Carte de crédit")

ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")


def _decoder(photo):
    """dataURL -> (bytes, mimetype)."""
    entete, _, contenu = (photo or "").partition(",")
    mimetype = "image/jpeg"
    m = re.match(r"data:([^;]+);", entete)
    if m:
        mimetype = m.group(1)
    return base64.b64decode(contenu or entete), mimetype


@frappe.whitelist()
def analyser(photo):
    """Lecture OpenAI de la photo de facture -> préremplissage du dialogue.
    L'employé garde la main : rien n'est créé ici."""
    frappe.only_for(ROLES)
    if not photo:
        frappe.throw(_("Aucune photo à analyser."))
    try:
        from bank_retenue_sync.ai.invoice_extract import extract_invoice_image
    except ImportError:
        frappe.throw(_("Le module d'extraction (bank_retenue_sync) n'est pas installé."))
    contenu, mimetype = _decoder(photo)
    d = extract_invoice_image(contenu, mimetype=mimetype,
                              extra_hint="Facture d'achat locale, TND.")
    return {
        "fournisseur": d.get("supplier_name") or "",
        "montant": flt(d.get("total_ttc"), 3),
        "numero": d.get("invoice_no") or "",
        "date": d.get("invoice_date") or "",
        "coherent": bool(d.get("_balanced")),
    }


@frappe.whitelist()
def creer(type_depense, montant, mode, compte=None, description=None, fournisseur=None,
          n_cheque=None, banque=None, photo_facture=None, photo_facture_nom=None,
          photo_cheque=None, photo_cheque_nom=None):
    """Crée et SOUMET l'écriture de dépense, photos attachées. Retourne son nom."""
    frappe.only_for(ROLES)
    montant = flt(montant, 3)
    description = (description or "").strip()
    if type_depense not in TYPES:
        frappe.throw(_("Type de dépense inconnu : {0}.").format(type_depense))
    if mode not in MODES:
        frappe.throw(_("Mode de paiement inconnu : {0}.").format(mode))
    if montant <= 0:
        frappe.throw(_("Le montant doit être positif."))
    if not description:
        frappe.throw(_("La description est obligatoire."))
    if type_depense != "Dépense non facturée" and not photo_facture:
        frappe.throw(_("Pour « {0} », la photo de la facture est obligatoire.")
                     .format(type_depense))
    n_cheque = (n_cheque or "").strip()
    if mode == "Chèque":
        if not re.fullmatch(r"\d{7}", n_cheque):
            frappe.throw(_("Le numéro de chèque doit comporter exactement 7 chiffres."))
        if not (banque or "").strip():
            frappe.throw(_("Pour un chèque, la banque est obligatoire."))
        if not photo_cheque:
            frappe.throw(_("Pour un chèque, la photo du chèque est obligatoire."))

    compte = (compte or "").strip() or COMPTE_DEPENSE_DEFAUT
    meta = frappe.db.get_value("Account", compte, ["root_type", "is_group"], as_dict=True)
    if not meta or meta.is_group or meta.root_type != "Expense":
        frappe.throw(_("{0} n'est pas un compte de charge utilisable.").format(compte))

    credit = COMPTE_ESPECES if mode == "Espèces" else COMPTE_BANQUE
    remarques = [description, _("Type : {0}").format(type_depense)]
    if fournisseur:
        remarques.append(_("Fournisseur : {0}").format(fournisseur.strip()))
    if mode == "Chèque":
        # La convention que lit l'identification bancaire (« Chq N° nnnnnnn »).
        remarques.append("Chq N° %s - Bq %s" % (n_cheque, (banque or "").strip()))
    elif mode == "Carte de crédit":
        remarques.append(_("Réglé par carte bancaire"))
    if type_depense == "Facture d'achat":
        remarques.append(_("FACTURE D'ACHAT — à intégrer au flux achat (TVA à récupérer)"))
    remarques.append(_("Saisie caisse par {0}").format(frappe.session.user))

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.company = COMPANY
    je.posting_date = nowdate()
    je.cheque_no = (_("Dépense caisse — {0}").format(description))[:140]
    je.cheque_date = nowdate()
    je.user_remark = "\n".join(remarques)
    je.append("accounts", {"account": credit, "credit_in_account_currency": montant,
                           "cost_center": CC})
    je.append("accounts", {"account": compte, "debit_in_account_currency": montant,
                           "cost_center": CC})
    je.insert(ignore_permissions=True)
    je.submit()

    from frappe.utils.file_manager import save_file
    if photo_facture:
        contenu, _mt = _decoder(photo_facture)
        save_file(photo_facture_nom or "facture.jpg", contenu,
                  "Journal Entry", je.name, is_private=1)
    if photo_cheque:
        contenu, _mt = _decoder(photo_cheque)
        save_file(photo_cheque_nom or f"cheque-{n_cheque}.jpg", contenu,
                  "Journal Entry", je.name, is_private=1)
    frappe.db.commit()
    return {"name": je.name, "montant": montant, "compte": compte}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def comptes_depense(doctype, txt, searchfield, start, page_len, filters):
    """Les comptes de CHARGE feuilles de la société, pour le champ compte du dialogue."""
    return frappe.db.sql(
        """
        SELECT name, account_name FROM `tabAccount`
        WHERE root_type = 'Expense' AND is_group = 0 AND disabled = 0
          AND company = %(company)s AND name LIKE %(txt)s
        ORDER BY name LIMIT %(start)s, %(page_len)s
        """,
        {"company": COMPANY, "txt": f"%{txt}%", "start": start, "page_len": page_len},
    )
