"""
Dépenses saisies depuis la caisse journalière.

TROIS TYPES (décisions utilisateur 19/08) :
  - « Dépense non facturée »  : saisie directe, compte de charge au choix ;
  - « Dépense avec facture »  : PHOTO obligatoire ; OpenAI CLASSIFIE la dépense
                                parmi les comptes de Charges Indirectes autorisés
                                (exclusions ci-dessous), et l'écriture se fait
                                Cr mode de paiement / Dr TVA / Dr compte classé ;
  - « Facture d'achat »       : PHOTO obligatoire ; la facture entre dans la file
                                « Facture Achat a Saisir » (le comptable la
                                transformera en vraie Purchase Invoice). Si elle
                                est PAYÉE, l'écriture va du compte de paiement
                                vers le FOURNISSEUR (Créditeurs) — la facture
                                saisie plus tard viendra la solder ; « Pas payé »
                                ne crée AUCUNE écriture, la dette naîtra avec la
                                facture.

MODES DE PAIEMENT ET COMPTES :
  - Espèces         -> Cr « Espèces - A&S » (la caisse) ;
  - Chèque          -> Cr Zitouna, n° à 7 CHIFFRES + banque + PHOTO du chèque ;
                       le n° cité en remarque est celui que lit l'identification
                       bancaire au débit « REGLEMENT CHEQUE nnnnnnn » ;
  - Carte de crédit -> Cr Zitouna, remarque dédiée (rapprochement montant+date) ;
  - Pas payé        -> réservé à « Facture d'achat », aucune écriture.

Les écritures sont SOUMISES, les photos attachées.
"""

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import flt, nowdate

COMPTE_ESPECES = "Espèces - A&S"
COMPTE_BANQUE = "STE430127B - Zitouna - A&S"
COMPTE_CREDITEURS = "Créditeurs - A&S"
COMPTE_DEPENSE_DEFAUT = "Dépenses non déclarées - A&S"
COMPANY = "Aquaworld & Servicing"
CC = "Principal - A&S"

# La TVA de la facture va sur le compte du taux lu (7 % ou, par défaut, 19 %).
COMPTE_TVA_19 = "TVA 19% - A&S"
COMPTE_TVA_7 = "TVA 7% - A&S"

TYPES = ("Dépense non facturée", "Dépense avec facture", "Facture d'achat")
MODES = ("Espèces", "Chèque", "Carte de crédit")
MODE_PAS_PAYE = "Pas payé"

ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")

# Classification OpenAI : les feuilles de « Charges Indirectes », SAUF les comptes
# techniques/pilotés (liste utilisateur 19/08) — jamais une dépense de caisse.
PARENT_CLASSIFICATION = "Charges Indirectes - A&S"
COMPTES_EXCLUS_CLASSIFICATION = {
    "Amortissement - A&S",
    "Arrondi - A&S",
    "Commission sur les ventes - A&S",
    "Déclaration comptable mensuelle - A&S",   # groupe : ses enfants avec lui
    "Dépenses non déclarées - A&S",
    "Gain/Perte sur Cessions des Immobilisations - A&S",
    "Perte de non paiement - A&S",
    "Profits / Pertes sur Change - A&S",
    "Reprise - A&S",
    "Salaire - A&S",
}


def _decoder(photo):
    """dataURL -> (bytes, mimetype)."""
    entete, _sep, contenu = (photo or "").partition(",")
    mimetype = "image/jpeg"
    m = re.match(r"data:([^;]+);", entete)
    if m:
        mimetype = m.group(1)
    return base64.b64decode(contenu or entete), mimetype


def comptes_classifiables():
    return [r[0] for r in frappe.db.sql(
        """SELECT name FROM `tabAccount`
           WHERE parent_account = %s AND is_group = 0 AND disabled = 0
             AND name NOT IN %s ORDER BY name""",
        (PARENT_CLASSIFICATION, tuple(COMPTES_EXCLUS_CLASSIFICATION)))]


def _classifier(image_bytes, mimetype, extraction):
    """Demande au modèle LE compte de charge de la dépense, parmi la liste fermée.
    Rend le nom du compte, ou None si la réponse sort de la liste."""
    from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

    comptes = comptes_classifiables()
    client, model, _t = _get_client_model_temp()
    b64 = base64.b64encode(image_bytes).decode()
    res = client.responses.create(
        model=model,
        instructions=(
            "Tu classes une dépense d'entreprise tunisienne dans un plan comptable. "
            "Réponds STRICTEMENT en JSON : {\"compte\": <un nom EXACT de la liste>}. "
            "Liste des comptes autorisés : " + json.dumps(comptes, ensure_ascii=False)),
        input=[{"role": "user", "content": [
            {"type": "input_image", "image_url": f"data:{mimetype};base64,{b64}"},
            {"type": "input_text",
             "text": "Facture : %s" % json.dumps(
                 {k: extraction.get(k) for k in ("supplier_name", "invoice_no", "total_ttc")},
                 ensure_ascii=False, default=str)}]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    try:
        compte = (json.loads(texte).get("compte") or "").strip()
    except Exception:
        return None
    return compte if compte in comptes else None


@frappe.whitelist()
def analyser(photo, type_depense=None):
    """Lecture OpenAI de la photo -> préremplissage. Pour « Dépense avec facture »,
    ajoute la CLASSIFICATION dans les Charges Indirectes autorisées.
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
    out = {
        "fournisseur": d.get("supplier_name") or "",
        "montant": flt(d.get("total_ttc"), 3),
        "tva": flt(d.get("total_tva"), 3),
        "taux_tva": flt(d.get("vat_rate"), 3),
        "numero": d.get("invoice_no") or "",
        "date": d.get("invoice_date") or "",
        "coherent": bool(d.get("_balanced")),
        "compte_suggere": None,
    }
    if type_depense == "Dépense avec facture":
        try:
            out["compte_suggere"] = _classifier(contenu, mimetype, d)
        except Exception:
            out["compte_suggere"] = None   # la classification est une aide, jamais un blocage
    return out


def _supplier(nom):
    """La fiche fournisseur correspondant au nom lu — retrouvée, sinon CRÉÉE (le
    comptable la complétera en saisissant la facture)."""
    nom = (nom or "").strip()
    if not nom:
        return None
    existant = (frappe.db.get_value("Supplier", {"supplier_name": nom})
                or frappe.db.get_value("Supplier",
                                       {"supplier_name": ["like", f"%{nom}%"]}))
    if existant:
        return existant
    doc = frappe.get_doc({"doctype": "Supplier", "supplier_name": nom})
    doc.insert(ignore_permissions=True)
    return doc.name


@frappe.whitelist()
def creer(type_depense, montant, mode, compte=None, description=None, fournisseur=None,
          tva=0, taux_tva=0, numero_facture=None, date_facture=None,
          n_cheque=None, banque=None, photo_facture=None, photo_facture_nom=None,
          photo_cheque=None, photo_cheque_nom=None):
    """Crée la dépense selon son type (voir l'en-tête du module). Retourne les noms
    des pièces créées (écriture et/ou fiche de la file des factures d'achat)."""
    frappe.only_for(ROLES)
    montant = flt(montant, 3)
    tva = flt(tva, 3)
    description = (description or "").strip()
    if type_depense not in TYPES:
        frappe.throw(_("Type de dépense inconnu : {0}.").format(type_depense))
    if mode == MODE_PAS_PAYE and type_depense != "Facture d'achat":
        frappe.throw(_("« Pas payé » est réservé aux factures d'achat."))
    if mode not in MODES and mode != MODE_PAS_PAYE:
        frappe.throw(_("Mode de paiement inconnu : {0}.").format(mode))
    if montant <= 0:
        frappe.throw(_("Le montant doit être positif."))
    if not description:
        frappe.throw(_("La description est obligatoire."))
    if type_depense != "Dépense non facturée" and not photo_facture:
        frappe.throw(_("Pour « {0} », la photo de la facture est obligatoire.")
                     .format(type_depense))
    if type_depense == "Facture d'achat" and not (fournisseur or "").strip():
        frappe.throw(_("Pour une facture d'achat, le fournisseur est obligatoire."))
    if tva < 0 or tva >= montant:
        frappe.throw(_("La TVA ({0}) doit rester inférieure au montant TTC ({1}).")
                     .format(tva, montant))
    n_cheque = (n_cheque or "").strip()
    if mode == "Chèque":
        if not re.fullmatch(r"\d{7}", n_cheque):
            frappe.throw(_("Le numéro de chèque doit comporter exactement 7 chiffres."))
        if not (banque or "").strip():
            frappe.throw(_("Pour un chèque, la banque est obligatoire."))
        if not photo_cheque:
            frappe.throw(_("Pour un chèque, la photo du chèque est obligatoire."))

    remarques = [description, _("Type : {0}").format(type_depense)]
    if fournisseur:
        remarques.append(_("Fournisseur : {0}").format(fournisseur.strip()))
    if numero_facture:
        remarques.append(_("Facture n° {0}").format(numero_facture))
    if mode == "Chèque":
        # La convention que lit l'identification bancaire (« Chq N° nnnnnnn »).
        remarques.append("Chq N° %s - Bq %s" % (n_cheque, (banque or "").strip()))
    elif mode == "Carte de crédit":
        remarques.append(_("Réglé par carte bancaire"))
    remarques.append(_("Saisie caisse par {0}").format(frappe.session.user))

    je = None
    if type_depense == "Facture d'achat":
        supplier = _supplier(fournisseur)
        if mode != MODE_PAS_PAYE:
            # Le paiement va au FOURNISSEUR (Créditeurs) : la facture saisie plus
            # tard le soldera — jamais de charge ici, elle naîtra avec la facture.
            je = _ecriture([
                {"account": COMPTE_ESPECES if mode == "Espèces" else COMPTE_BANQUE,
                 "credit_in_account_currency": montant, "cost_center": CC},
                {"account": COMPTE_CREDITEURS, "party_type": "Supplier",
                 "party": supplier, "debit_in_account_currency": montant,
                 "cost_center": CC},
            ], description, remarques)
        fiche = frappe.get_doc({
            "doctype": "Facture Achat a Saisir",
            "fournisseur": (fournisseur or "").strip(),
            "supplier": supplier,
            "montant": montant,
            "numero_facture": (numero_facture or "").strip(),
            "date_facture": date_facture or None,
            "mode_paiement": mode,
            "journal_entry": je.name if je else None,
            "saisi_par": frappe.session.user,
            "description": description,
        })
        fiche.insert(ignore_permissions=True)
        _attacher(photo_facture, photo_facture_nom or "facture.jpg",
                  "Facture Achat a Saisir", fiche.name)
        if je:
            _attacher(photo_facture, photo_facture_nom or "facture.jpg",
                      "Journal Entry", je.name)
        resultat = {"name": je.name if je else None, "fiche": fiche.name}
    else:
        compte = (compte or "").strip() or COMPTE_DEPENSE_DEFAUT
        meta = frappe.db.get_value("Account", compte, ["root_type", "is_group"], as_dict=True)
        if not meta or meta.is_group or meta.root_type != "Expense":
            frappe.throw(_("{0} n'est pas un compte de charge utilisable.").format(compte))
        lignes = [{"account": COMPTE_ESPECES if mode == "Espèces" else COMPTE_BANQUE,
                   "credit_in_account_currency": montant, "cost_center": CC}]
        if type_depense == "Dépense avec facture" and tva > 0:
            # Règle utilisateur (19/08) : la TVA va sur le compte de son taux — 7 % sur
            # « TVA 7% », TOUT AUTRE taux (19, 13, inconnu, mixte) sur « TVA 19% ».
            # Le TIMBRE FISCAL et toute autre charge hors TVA restent dans le compte de
            # charges indirectes classé : Dr charge = TTC − TVA, jamais TTC − TVA − timbre.
            compte_tva = COMPTE_TVA_7 if flt(taux_tva) == 7 else COMPTE_TVA_19
            lignes.append({"account": compte_tva, "debit_in_account_currency": tva,
                           "cost_center": CC})
            lignes.append({"account": compte,
                           "debit_in_account_currency": round(montant - tva, 3),
                           "cost_center": CC})
        else:
            lignes.append({"account": compte, "debit_in_account_currency": montant,
                           "cost_center": CC})
        je = _ecriture(lignes, description, remarques)
        if photo_facture:
            _attacher(photo_facture, photo_facture_nom or "facture.jpg",
                      "Journal Entry", je.name)
        resultat = {"name": je.name, "fiche": None}

    if photo_cheque and je:
        _attacher(photo_cheque, photo_cheque_nom or f"cheque-{n_cheque}.jpg",
                  "Journal Entry", je.name)
    frappe.db.commit()
    return resultat


def _ecriture(lignes, description, remarques):
    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.company = COMPANY
    je.posting_date = nowdate()
    je.cheque_no = (_("Dépense caisse — {0}").format(description))[:140]
    je.cheque_date = nowdate()
    je.user_remark = "\n".join(remarques)
    for ligne in lignes:
        je.append("accounts", ligne)
    je.insert(ignore_permissions=True)
    je.submit()
    return je


def _attacher(photo, nom, doctype, name):
    from frappe.utils.file_manager import save_file
    contenu, _mt = _decoder(photo)
    save_file(nom, contenu, doctype, name, is_private=1)


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
