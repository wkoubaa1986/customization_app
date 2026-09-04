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
from frappe.utils import cint, flt, nowdate

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

# Retenue à la source achat : la règle, le seuil et le taux vivent dans bank_retenue_sync
# (`achat/retenue_depense`), avec ceux des factures d'achat. Ici on ne fait que les appliquer.
COMPTE_RETENUE_ACHAT = "Retenue a la source achat - A&S"


def _retenue_proposee(type_depense, montant, timbre=0):
    """Le montant que la règle propose, ou 0. Ne lève jamais : l'app doit rester utilisable
    même si bank_retenue_sync n'est pas installée sur ce bench."""
    try:
        from bank_retenue_sync.achat import retenue_depense as R

        return flt(R.retenue_due(montant, timbre, type_depense), 3)
    except Exception:
        return 0.0


def _compte_retenue():
    try:
        from bank_retenue_sync.achat import retenue_depense as R

        return R.compte()
    except Exception:
        return COMPTE_RETENUE_ACHAT
MODES = ("Espèces", "Chèque", "Carte de crédit")
MODE_PAS_PAYE = "Pas payé"

# Dépense avec facture NON PAYÉE : la charge est comptabilisée tout de suite
# CONTRE LE DÉCOUVERT (décision utilisateur 24/08) — la dette reste visible
# jusqu'au règlement, généré par `solder_depense`.
COMPTE_DECOUVERT = "Compte de découvert bancaire - A&S"

# Préfixes des écritures de caisse. Le rapport de caisse ne lit QUE le premier :
# une dépense « à payer » n'y entre qu'au jour de son règlement.
PREFIXE_CAISSE = "Dépense caisse — "
PREFIXE_A_PAYER = "Dépense à payer — "

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


def _est_pdf(contenu, mimetype=None):
    """Le justificatif est-il DÉJÀ un PDF ? (fournisseurs qui envoient des PDF)

    ⚠️ UN PDF N'EST PAS UNE PHOTO. Pillow ne sait pas l'ouvrir (« cannot identify
    image file », vu le 20/08/2026 sur « Fac N° 2026-0312 -TELE TRACK.pdf ») :
    pas de cadrage, pas de redressement, et l'analyse passe par la voie PDF du
    modèle. Un PDF est déjà un document propre — il s'attache tel quel."""
    return (contenu or b"").startswith(b"%PDF") or (mimetype or "") == "application/pdf"


def _bloc_document(contenu, mimetype):
    """Le bloc d'entrée du modèle pour ce justificatif : image ou PDF.

    L'API Responses refuse un PDF en `input_image` et une image en `input_file` —
    c'est le contenu qui décide, jamais l'appelant."""
    b64 = base64.b64encode(contenu).decode()
    if _est_pdf(contenu, mimetype):
        return {"type": "input_file", "filename": "justificatif.pdf",
                "file_data": "data:application/pdf;base64,%s" % b64}
    return {"type": "input_image",
            "image_url": "data:%s;base64,%s" % (mimetype or "image/jpeg", b64)}


def _consigne_matricule():
    """La phrase qui empêche le modèle de rendre NOTRE matricule au lieu de celui
    du fournisseur."""
    notre = (frappe.db.get_value("Company", COMPANY, "tax_id") or "").strip()
    if not notre:
        return ""
    return (" Le matricule %s est celui du CLIENT (%s) : ne le rends jamais comme "
            "matricule du fournisseur." % (notre, COMPANY))


def comptes_classifiables():
    return [r[0] for r in frappe.db.sql(
        """SELECT name FROM `tabAccount`
           WHERE parent_account = %s AND is_group = 0 AND disabled = 0
             AND name NOT IN %s ORDER BY name""",
        (PARENT_CLASSIFICATION, tuple(COMPTES_EXCLUS_CLASSIFICATION)))]


def _classifier(image_bytes, mimetype, extraction):
    """Demande au modèle LE compte de charge de la dépense ET sa description lue
    sur la photo (même appel : pas de latence en plus). -> (compte, description)
    — compte None si la réponse sort de la liste, description '' si muette."""
    from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

    comptes = comptes_classifiables()
    client, model, _t = _get_client_model_temp()
    res = client.responses.create(
        model=model,
        instructions=(
            "Tu classes une dépense d'entreprise tunisienne dans un plan comptable et tu la "
            "résumes. Réponds STRICTEMENT en JSON : "
            "{\"compte\": <un nom EXACT de la liste>, "
            "\"description\": <ce qui a été acheté, lu sur le document, en français, "
            "3 à 10 mots, sans montant ni date>, "
            "\"matricule\": <le matricule fiscal de l'ÉMETTEUR de la facture "
            "(le fournisseur), tel qu'écrit ; null si absent>, "
            "\"type_document\": <\"facture\" ou \"bon_de_livraison\" — un bon "
            "de livraison porte la mention BL / Bon de livraison / Delivery "
            "note et n'a pas de numéro de facture ni de mention TVA à payer>, "
            "\"numero_bl\": <le numéro du bon de livraison s'il s'agit d'un "
            "BL, tel qu'écrit ; null sinon>}. "
            + _consigne_matricule()
            + " Liste des comptes autorisés : " + json.dumps(comptes, ensure_ascii=False)),
        input=[{"role": "user", "content": [
            _bloc_document(image_bytes, mimetype),
            {"type": "input_text",
             "text": "Facture : %s" % json.dumps(
                 {k: extraction.get(k) for k in ("supplier_name", "invoice_no", "total_ttc")},
                 ensure_ascii=False, default=str)}]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    try:
        lu = json.loads(texte)
        compte = (lu.get("compte") or "").strip()
        description = (lu.get("description") or "").strip()
        matricule = (lu.get("matricule") or "").strip()
        type_doc = (lu.get("type_document") or "").strip()
        numero_bl = (lu.get("numero_bl") or "").strip()
    except Exception:
        return None, "", "", "", ""
    return ((compte if compte in comptes else None), description, matricule,
            type_doc, numero_bl)


def _decrire(image_bytes, mimetype):
    """La description de la dépense ET le matricule fiscal du FOURNISSEUR, lus sur
    le document. -> (description, matricule) — chaînes vides si le modèle est muet.

    ⚠️ LE MATRICULE DU FOURNISSEUR, JAMAIS CELUI DU CLIENT. Une facture porte les
    deux : celui de l'émetteur et le nôtre. Confondre les deux rapprocherait
    toutes les factures sur une seule fiche."""
    from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

    client, model, _t = _get_client_model_temp()
    res = client.responses.create(
        model=model,
        instructions=(
            "Tu lis le document photographié d'une dépense d'entreprise "
            "tunisienne : facture, reçu, ou BON DE LIVRAISON. Réponds "
            "STRICTEMENT en JSON : "
            "{\"description\": <ce qui a été acheté, en français, 3 à 10 mots, "
            "sans montant ni date>, "
            "\"matricule\": <le matricule fiscal de l'ÉMETTEUR du document "
            "(le fournisseur), tel qu'écrit ; null si absent>, "
            "\"type_document\": <\"facture\" ou \"bon_de_livraison\" — un bon "
            "de livraison porte la mention BL / Bon de livraison / Delivery "
            "note et N'A PAS de numéro de facture ni de mention TVA à payer>, "
            "\"numero_bl\": <le numéro du bon de livraison s'il s'agit d'un "
            "BL, tel qu'écrit ; null sinon>}. "
            + _consigne_matricule()),
        input=[{"role": "user", "content": [
            _bloc_document(image_bytes, mimetype),
            {"type": "input_text",
             "text": "Décris la dépense, lis le matricule, et dis si c'est "
                     "une facture ou un bon de livraison."}]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    try:
        lu = json.loads(texte)
        return ((lu.get("description") or "").strip(),
                (lu.get("matricule") or "").strip(),
                (lu.get("type_document") or "").strip(),
                (lu.get("numero_bl") or "").strip())
    except Exception:
        return "", "", "", ""


@frappe.whitelist()
def analyser(photo, type_depense=None):
    """Lecture OpenAI de la photo -> préremplissage. Pour « Dépense avec facture »,
    ajoute la CLASSIFICATION dans les Charges Indirectes autorisées.
    L'employé garde la main : rien n'est créé ici."""
    frappe.only_for(ROLES)
    if not photo:
        frappe.throw(_("Aucune photo à analyser."))
    try:
        from bank_retenue_sync.ai.invoice_extract import (extract_invoice_image,
                                                          extract_invoice_scan)
    except ImportError:
        frappe.throw(_("Le module d'extraction (bank_retenue_sync) n'est pas installé."))
    contenu, mimetype = _decoder(photo)
    # Un PDF part TEL QUEL au modèle (voie « scan ») : l'API refuse un PDF en
    # image, et rien n'est installé ici pour le rasteriser.
    if _est_pdf(contenu, mimetype):
        d = extract_invoice_scan(contenu, extra_hint="Facture d'achat locale, TND.")
    else:
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
        "description": "",
        "matricule": "",
        # Rapprochement fournisseur : rempli pour la FACTURE D'ACHAT seulement —
        # c'est le seul type qui crée ou rattache une fiche fournisseur
        # (décision utilisateur 2026-08-20).
        "fournisseur_certain": None,
        "fournisseur_motif": "",
        "fournisseur_candidats": [],
        # Détection BL (décision utilisateur 24/08) : c'est l'ANALYSE qui dit si
        # le document est une facture ou un bon de livraison — la fiche est
        # marquée BL et la facture qui le couvre sera rattachée plus tard.
        "est_bl": False,
        "numero_bl": "",
    }
    if type_depense == "Dépense avec facture":
        try:
            (out["compte_suggere"], out["description"], out["matricule"],
             type_doc, numero_bl) = _classifier(contenu, mimetype, d)
            # BL aussi pour les dépenses facturées (décision utilisateur 24/08) :
            # la trace « BL n°X » suit la dépense dans description et remarques.
            if type_doc == "bon_de_livraison":
                out["est_bl"] = True
                out["numero_bl"] = numero_bl
        except Exception:
            out["compte_suggere"] = None   # la classification est une aide, jamais un blocage
    else:
        # Pas de classification pour une facture d'achat, mais la description lue
        # sur la photo sert autant (décision utilisateur 2026-08-20).
        try:
            desc, matricule, type_doc, numero_bl = _decrire(contenu, mimetype)
            out["description"], out["matricule"] = desc, matricule
            if type_depense == "Facture d'achat" and type_doc == "bon_de_livraison":
                out["est_bl"] = True
                out["numero_bl"] = numero_bl
        except Exception:
            pass
    # ⚠️ FOURNISSEUR ET N° DE FACTURE TOUJOURS DANS LA DESCRIPTION (décision
    # utilisateur 2026-08-20) : elle devient le « N° de référence » de l'écriture
    # de journal (« Dépense caisse — … ») — c'est par elle qu'on retrouve la pièce.
    morceaux = [m for m in (out["description"], out["fournisseur"]) if m]
    if out["est_bl"] and out["numero_bl"]:
        morceaux.append(_("BL n°{0}").format(out["numero_bl"]))
    elif out["numero"]:
        morceaux.append(_("Fact. n°{0}").format(out["numero"]))
    out["description"] = " — ".join(morceaux)

    if type_depense == "Facture d'achat" and out["fournisseur"]:
        r = _rapprocher_fournisseur(out["fournisseur"], out["matricule"])
        out["fournisseur_certain"] = r["certain"]
        out["fournisseur_motif"] = r["motif"]
        out["fournisseur_candidats"] = r["candidats"]
    return out


#: Les formes juridiques et abréviations qui ne distinguent pas deux fournisseurs :
#: « STE TOTAL TUNISIE SARL » et « Total Tunisie » sont le même.
_FORMES_JURIDIQUES = ("STE", "SOCIETE", "SARL", "SUARL", "SA", "SNC", "ETS",
                      "ETABLISSEMENT", "ETABLISSEMENTS", "EURL", "SPA", "SASU", "SAS")


def _sans_accents(txt):
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(c) != "Mn")


def cle_matricule(valeur):
    """La partie DISCRIMINANTE d'un matricule fiscal tunisien. Fonction pure.

    « 1137847 D/B/M 000 », « 1137847D/B/M/000 » et « 1137847 » désignent le même
    contribuable : ce sont les 7 premiers chiffres qui identifient, le reste est
    la clé, le code catégorie et le numéro d'établissement. Comparer les chaînes
    brutes ferait passer le même fournisseur pour deux."""
    chiffres = re.sub(r"\D", "", str(valeur or ""))
    return chiffres[:7] if len(chiffres) >= 7 else ""


def cle_nom(nom):
    """Le nom d'un fournisseur réduit à ce qui le distingue. Fonction pure."""
    txt = _sans_accents(str(nom or "")).upper()
    txt = re.sub(r"[^A-Z0-9 ]+", " ", txt)
    mots = [m for m in txt.split() if m and m not in _FORMES_JURIDIQUES]
    return " ".join(mots)


def _rapprocher_fournisseur(nom, matricule=None):
    """Le fournisseur de cette facture, parmi ceux qui existent. -> dict.

    ⚠️ LE MATRICULE FISCAL TRANCHE, LE NOM SUGGÈRE. Deux fiches pour le même
    fournisseur, c'est un solde éclaté sur deux comptes auxiliaires et une TVA
    déductible qu'on ne sait plus rattacher. Le matricule identifie le
    contribuable : quand il concorde, c'est certain. Le nom, lui, s'écrit de dix
    façons — il ne donne qu'une CERTITUDE quand il est identique une fois
    normalisé, et sinon des CANDIDATS que l'utilisateur tranche.

    -> {certain: nom de la fiche ou None, motif, candidats: [...], matricule}
    """
    from difflib import SequenceMatcher

    nom = (nom or "").strip()
    cle_m = cle_matricule(matricule)
    fiches = frappe.get_all("Supplier", fields=["name", "supplier_name", "tax_id"],
                            filters={"disabled": 0}, limit_page_length=0)

    if cle_m:
        for f in fiches:
            if cle_matricule(f.tax_id) == cle_m:
                return {"certain": f.name, "motif": "matricule", "candidats": [],
                        "matricule": matricule}

    cible = cle_nom(nom)
    if not cible:
        return {"certain": None, "motif": "", "candidats": [], "matricule": matricule}

    candidats = []
    for f in fiches:
        cle_f = cle_nom(f.supplier_name or f.name)
        if not cle_f:
            continue
        if cle_f == cible:
            return {"certain": f.name, "motif": "nom", "candidats": [],
                    "matricule": matricule}
        score = SequenceMatcher(None, cible, cle_f).ratio()
        if cle_f in cible or cible in cle_f:
            score = max(score, 0.9)
        if score >= 0.7:
            candidats.append({"name": f.name, "supplier_name": f.supplier_name or f.name,
                              "tax_id": f.tax_id or "", "score": round(score, 3)})
    candidats.sort(key=lambda c: c["score"], reverse=True)
    return {"certain": None, "motif": "", "candidats": candidats[:5],
            "matricule": matricule}


@frappe.whitelist()
def fournisseurs_candidats(nom, matricule=None):
    """Le rapprochement, pour que l'écran demande à l'utilisateur en cas de doute."""
    frappe.only_for(ROLES)
    return _rapprocher_fournisseur(nom, matricule)


def _poser_matricule(supplier, matricule):
    """Complète le matricule fiscal d'une fiche qui n'en a pas. N'écrase jamais."""
    cle_m = cle_matricule(matricule)
    if not (supplier and cle_m):
        return
    actuel = frappe.db.get_value("Supplier", supplier, "tax_id")
    if not (actuel or "").strip():
        frappe.db.set_value("Supplier", supplier, "tax_id", (matricule or "").strip(),
                            update_modified=False)


def _supplier(nom, matricule=None, supplier=None):
    """La fiche fournisseur de cette facture : celle choisie, celle rapprochée avec
    certitude, ou une NOUVELLE — jamais un doublon créé en silence.

    ⚠️ EN CAS DE DOUTE ON REFUSE, ON NE CRÉE PAS (décision utilisateur
    2026-08-20). Des fiches proches existent : c'est à l'utilisateur de dire si
    c'est l'une d'elles ou un fournisseur nouveau. L'écran le lui demande ; ce
    garde-fou est là pour les appels qui ne passeraient pas par lui."""
    nom = (nom or "").strip()
    if supplier:
        if not frappe.db.exists("Supplier", supplier):
            frappe.throw(_("Le fournisseur {0} n'existe pas.").format(supplier))
        _poser_matricule(supplier, matricule)
        return supplier
    if not nom:
        return None

    r = _rapprocher_fournisseur(nom, matricule)
    if r["certain"]:
        _poser_matricule(r["certain"], matricule)
        return r["certain"]
    if r["candidats"]:
        frappe.throw(_("Fournisseur à confirmer : « {0} » ressemble à {1}. "
                       "Choisissez la fiche existante ou confirmez la création "
                       "d'un nouveau fournisseur.")
                     .format(nom, ", ".join(c["supplier_name"] for c in r["candidats"])))

    doc = frappe.get_doc({"doctype": "Supplier", "supplier_name": nom})
    if cle_matricule(matricule):
        doc.tax_id = (matricule or "").strip()
    doc.insert(ignore_permissions=True)
    return doc.name


def _paiements_normalises(montant, mode, n_cheque=None, banque=None,
                          photo_cheque=None, photo_cheque_nom=None, paiements=None):
    """La liste normalisée des règlements d'une dépense.

    `paiements` (JSON) permet le PAIEMENT FRACTIONNÉ (plusieurs chèques, ou
    espèces + chèque — décision utilisateur 24/08) ; sans lui, l'ancien chemin
    mono-mode est reconstruit à l'identique. Valide : somme = montant, et
    chaque chèque complet (7 chiffres, banque, photo)."""
    if isinstance(paiements, str) and paiements.strip():
        paiements = json.loads(paiements)
    if not paiements:
        paiements = [{"mode": mode, "montant": montant, "n_cheque": n_cheque,
                      "banque": banque, "photo_cheque": photo_cheque,
                      "photo_cheque_nom": photo_cheque_nom}]
    lignes = []
    total = 0.0
    for p in paiements:
        m = p.get("mode")
        # « Pas payé » accepté COMME LIGNE (paiement partiel, décision 24/08) :
        # la part payée sort de la caisse, le reste part en dette.
        if m not in MODES and m != MODE_PAS_PAYE:
            frappe.throw(_("Mode de paiement inconnu : {0}.").format(m))
        mt = flt(p.get("montant"), 3)
        if mt <= 0:
            frappe.throw(_("Chaque règlement doit avoir un montant positif."))
        nc = (p.get("n_cheque") or "").strip()
        if m == "Chèque":
            if not re.fullmatch(r"\d{7}", nc):
                frappe.throw(_("Le numéro de chèque doit comporter exactement 7 chiffres."))
            if not (p.get("banque") or "").strip():
                frappe.throw(_("Pour un chèque, la banque est obligatoire."))
            if not p.get("photo_cheque"):
                frappe.throw(_("Pour un chèque, la photo du chèque est obligatoire."))
        total += mt
        lignes.append({"mode": m, "montant": mt, "n_cheque": nc,
                       "banque": (p.get("banque") or "").strip(),
                       "photo_cheque": p.get("photo_cheque"),
                       "photo_cheque_nom": p.get("photo_cheque_nom")})
    if abs(total - flt(montant, 3)) > 0.001:
        frappe.throw(_("La somme des règlements ({0}) doit égaler le montant TTC ({1}).")
                     .format(flt(total, 3), flt(montant, 3)))
    return lignes


def _lignes_credit(regs):
    """Les lignes CRÉDITÉES de l'écriture : une par règlement PAYÉ."""
    return [{"account": COMPTE_ESPECES if p["mode"] == "Espèces" else COMPTE_BANQUE,
             "credit_in_account_currency": p["montant"], "cost_center": CC}
            for p in regs if p["mode"] != MODE_PAS_PAYE]


def _partage_paiements(regs):
    """(lignes payées, total non payé) d'une liste de règlements."""
    paid = [p for p in regs if p["mode"] != MODE_PAS_PAYE]
    unpaid = flt(sum(p["montant"] for p in regs if p["mode"] == MODE_PAS_PAYE), 3)
    return paid, unpaid


def _remarques_paiements(remarques, regs):
    """Complète les remarques avec chaque chèque (convention « Chq N° » lue par
    l'identification bancaire) et la mention carte."""
    for p in regs:
        if p["mode"] == "Chèque":
            remarques.append("Chq N° %s - Bq %s" % (p["n_cheque"], p["banque"]))
        elif p["mode"] == "Carte de crédit":
            remarques.append(_("Réglé par carte bancaire"))
    return remarques


def _attacher_cheques(regs, je_nom):
    """Attache la photo de chaque chèque à l'écriture."""
    for p in regs:
        if p.get("photo_cheque"):
            _attacher(p["photo_cheque"],
                      p.get("photo_cheque_nom") or f"cheque-{p['n_cheque']}.jpg",
                      "Journal Entry", je_nom)


def _mode_global(regs):
    """Le mode affiché sur la fiche : le mode unique, sinon « Mixte »."""
    modes = {p["mode"] for p in regs}
    if not modes:
        return MODE_PAS_PAYE
    return regs[0]["mode"] if len(modes) == 1 else "Mixte"


@frappe.whitelist()
def proposition_retenue(type_depense=None, montant=0, timbre=0, fournisseur=None,
                        supplier=None):
    """Ce que la fenêtre doit pré-remplir : la retenue, le net, et ce qui manque pour le
    certificat. N'écrit rien.

    ⚠️ ON POSE LA RETENUE MÊME SANS MATRICULE, ET ON LE DIT (décision utilisateur 04/09/2026).
    Bloquer la saisie sur une fiche fournisseur incomplète arrêterait la caisse pour une raison
    administrative ; taire le problème laisserait une retenue qu'on ne pourrait jamais déclarer.
    Le certificat part par la file, une fois la fiche complétée.
    """
    frappe.only_for(ROLES)
    montant = flt(montant, 3)
    retenue = _retenue_proposee(type_depense, montant, flt(timbre, 3))
    out = {"retenue": retenue, "net": flt(montant - retenue, 3),
           "compte": _compte_retenue(), "avertissements": [], "supplier": supplier or None}
    if retenue <= 0:
        return out

    if not out["supplier"] and (fournisseur or "").strip():
        r = _rapprocher_fournisseur(fournisseur, None)
        out["supplier"] = r.get("certain")
        out["fournisseur_candidats"] = r.get("candidats") or []
    if not out["supplier"]:
        out["avertissements"].append(
            _("Aucune fiche fournisseur ne correspond à « {0} » : la retenue sera bien posée, "
              "mais le certificat ne pourra pas être émis tant que le fournisseur n'existe pas.")
            .format((fournisseur or "").strip() or "?"))
        return out

    mat = frappe.db.get_value("Supplier", out["supplier"], "tax_id")
    out["matricule"] = mat or ""
    if not (mat or "").strip():
        out["avertissements"].append(
            _("Le fournisseur {0} n'a pas de matricule fiscal : la retenue sera posée, mais le "
              "certificat TEJ ne pourra pas être émis avant que sa fiche soit complétée.")
            .format(out["supplier"]))
    return out


@frappe.whitelist()
def creer(type_depense, montant, mode, compte=None, description=None, fournisseur=None,
          tva=0, taux_tva=0, numero_facture=None, date_facture=None,
          n_cheque=None, banque=None, photo_facture=None, photo_facture_nom=None,
          photo_cheque=None, photo_cheque_nom=None, coins_facture=None,
          supplier=None, matricule=None, paiements=None, est_bl=0, numero_bl=None,
          retenue=None):
    """Crée la dépense selon son type (voir l'en-tête du module). Retourne les noms
    des pièces créées (écriture et/ou fiche de la file des factures d'achat)."""
    frappe.only_for(ROLES)
    montant = flt(montant, 3)
    tva = flt(tva, 3)
    description = (description or "").strip()
    # Le cadrage validé à l'écran (4 coins, pixels pleine taille) — voir
    # `detecter_contour` : quand il est là, il fait foi sur la détection.
    if isinstance(coins_facture, str) and coins_facture.strip():
        coins_facture = json.loads(coins_facture)
    if not coins_facture:
        coins_facture = None
    if type_depense not in TYPES:
        frappe.throw(_("Type de dépense inconnu : {0}.").format(type_depense))
    if mode == MODE_PAS_PAYE and type_depense == "Dépense non facturée":
        frappe.throw(_("« Pas payé » est réservé aux dépenses facturées."))
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

    # RETENUE À LA SOURCE ACHAT (décision utilisateur 04/09/2026). Elle n'est pas un moyen de
    # paiement : c'est la part que l'on NE verse PAS au fournisseur et qui reste due au Trésor.
    # Les règlements doivent donc couvrir le TTC MOINS la retenue, pas le TTC.
    #
    # ⚠️ LE MONTANT SEUL NE DÉCLENCHE RIEN. Trois des quatre écritures de plus de 1 000 DT
    # passées en caisse depuis le 01/09 sont des primes de salariés : y retenir 1 % serait faux.
    # C'est `retenue_depense.assujettie` qui tranche, sur le TYPE autant que sur le montant.
    retenue = flt(retenue, 3) if retenue not in (None, "") else _retenue_proposee(type_depense,
                                                                                 montant)
    if retenue < 0 or retenue >= montant:
        frappe.throw(_("La retenue ({0}) doit rester inférieure au montant TTC ({1}).")
                     .format(retenue, montant))
    a_regler = flt(montant - retenue, 3)
    # Les règlements (mono-mode ou fractionnés). Une ligne « Pas payé » dans le
    # fractionné = paiement PARTIEL : la part payée sort de la caisse, le reste
    # part en dette (découvert pour une dépense, Créditeurs différés pour une
    # facture d'achat).
    if mode == MODE_PAS_PAYE:
        regs, paid, unpaid = [], [], a_regler
    else:
        regs = _paiements_normalises(a_regler, mode, n_cheque, banque,
                                     photo_cheque, photo_cheque_nom, paiements)
        paid, unpaid = _partage_paiements(regs)
    if unpaid > 0 and type_depense == "Dépense non facturée":
        frappe.throw(_("« Pas payé » est réservé aux dépenses facturées."))

    remarques = [description, _("Type : {0}").format(type_depense)]
    if fournisseur:
        remarques.append(_("Fournisseur : {0}").format(fournisseur.strip()))
    if numero_facture:
        remarques.append(_("Facture n° {0}").format(numero_facture))
    if cint(est_bl) and (numero_bl or "").strip():
        remarques.append(_("BL n° {0}").format((numero_bl or "").strip()))
    if retenue > 0:
        # ⚠️ C'EST CETTE MENTION QUE LA FILE DES CERTIFICATS LIT. Sans champ dédié sur l'écriture,
        # la remarque est la seule mémoire de la retenue et de sa base — comme la convention
        # « Chq N° » que l'identification bancaire lit déjà.
        remarques.append(_("Retenue à la source achat : {0} sur {1} TTC")
                         .format(retenue, montant))
    _remarques_paiements(remarques, paid)
    if unpaid > 0 and paid:
        remarques.append(_("Reste à payer : {0}").format(unpaid))
    remarques.append(_("Saisie caisse par {0}").format(frappe.session.user))

    je = None
    if type_depense == "Facture d'achat":
        supplier = _supplier(fournisseur, matricule=matricule, supplier=supplier)
        paid_total = flt(sum(p["montant"] for p in paid), 3)
        if paid:
            # Le paiement va au FOURNISSEUR (Créditeurs) : la facture saisie plus
            # tard le soldera — jamais de charge ici, elle naîtra avec la facture.
            # Avec une part « Pas payé », l'avance ne couvre QUE la part payée :
            # le reste de la dette naîtra avec la facture.
            credits = _lignes_credit(paid)
            if retenue > 0:
                credits.append({"account": _compte_retenue(),
                                "credit_in_account_currency": retenue, "cost_center": CC})
            je = _ecriture(credits + [
                {"account": COMPTE_CREDITEURS, "party_type": "Supplier",
                 "party": supplier,
                 "debit_in_account_currency": flt(paid_total + retenue, 3),
                 "cost_center": CC},
            ], description, remarques)
        fiche = frappe.get_doc({
            "doctype": "Facture Achat a Saisir",
            "fournisseur": (fournisseur or "").strip(),
            "supplier": supplier,
            "montant": montant,
            "numero_facture": (numero_facture or "").strip(),
            "date_facture": date_facture or None,
            "est_bl": cint(est_bl),
            "numero_bl": (numero_bl or "").strip(),
            "mode_paiement": _mode_global(regs) if unpaid == 0 else
                             ("Pas payé" if not paid else "Mixte"),
            "journal_entry": je.name if je else None,
            "saisi_par": frappe.session.user,
            "description": description,
        })
        fiche.insert(ignore_permissions=True)
        _attacher_scan(photo_facture, "facture-%s" % fiche.name,
                       "Facture Achat a Saisir", fiche.name, coins=coins_facture)
        if je:
            _attacher_scan(photo_facture, "facture-%s" % fiche.name,
                           "Journal Entry", je.name, coins=coins_facture)
        resultat = {"name": je.name if je else None, "fiche": fiche.name}
    else:
        compte = (compte or "").strip()
        if not compte:
            if type_depense == "Dépense avec facture":
                # Jamais de repli silencieux ici : le compte vient de la
                # classification (ou d'un choix explicite de l'employé).
                frappe.throw(_("Choisissez le compte de charge — le bouton "
                               "« Analyser la facture » le propose."))
            compte = COMPTE_DEPENSE_DEFAUT
        meta = frappe.db.get_value("Account", compte, ["root_type", "is_group"], as_dict=True)
        if not meta or meta.is_group or meta.root_type != "Expense":
            frappe.throw(_("{0} n'est pas un compte de charge utilisable.").format(compte))
        # Crédits : la part payée sort de la caisse/banque ; la part « pas
        # payé » est portée par le DÉCOUVERT (la dette reste visible jusqu'au
        # règlement par `solder_depense`).
        lignes = _lignes_credit(paid)
        if retenue > 0:
            # La retenue n'est versée à personne : elle reste due au Trésor, et c'est nous qui
            # la déclarerons. Elle vient donc en CRÉDIT à côté du paiement, jamais en charge.
            lignes.append({"account": _compte_retenue(),
                           "credit_in_account_currency": retenue, "cost_center": CC})
        if unpaid > 0:
            lignes.append({"account": COMPTE_DECOUVERT,
                           "credit_in_account_currency": unpaid, "cost_center": CC})
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
        # Sans aucune part payée, l'écriture ne touche pas la caisse : préfixe
        # « à payer » → invisible du rapport jusqu'au règlement.
        je = _ecriture(lignes, description, remarques,
                       prefixe=PREFIXE_A_PAYER if not paid else None)
        if photo_facture:
            _attacher_scan(photo_facture, "facture-%s" % je.name,
                           "Journal Entry", je.name, coins=coins_facture)
        resultat = {"name": je.name, "fiche": None}
        # Une fiche naît s'il reste à payer OU si le justificatif est un BL —
        # même payée intégralement (décision utilisateur 24/08) : c'est elle qui
        # rend les dépenses sur BL LISTABLES (la dépense payée cash n'a sinon
        # qu'une écriture, invisible des listes).
        if unpaid > 0 or (cint(est_bl) and type_depense == "Dépense avec facture"):
            paye = unpaid <= 0
            fiche = frappe.get_doc({
                "doctype": "Depense A Payer",
                "statut": "Payée" if paye else "À payer",
                "description": description,
                "montant": montant if paye else unpaid,
                "tva": tva if not paid else 0,
                "taux_tva": flt(taux_tva),
                "compte_charge": compte,
                "fournisseur": (fournisseur or "").strip(),
                "numero_facture": (numero_facture or "").strip(),
                "date_facture": date_facture or None,
                "est_bl": cint(est_bl),
                "numero_bl": (numero_bl or "").strip(),
                "journal_entry": je.name,
                "reglement_journal_entry": je.name if paye else None,
                "date_reglement": nowdate() if paye else None,
                "saisi_par": frappe.session.user,
            })
            fiche.insert(ignore_permissions=True)
            _attacher_scan(photo_facture, "facture-%s" % fiche.name,
                           "Depense A Payer", fiche.name, coins=coins_facture)
            resultat = {"name": je.name, "fiche": fiche.name,
                        "a_payer": not paye, "reste": unpaid if not paye else 0}

    if je:
        _attacher_cheques(regs, je.name)
    frappe.db.commit()
    return resultat


def _ecriture(lignes, description, remarques, prefixe=None, date=None):
    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.company = COMPANY
    je.posting_date = date or nowdate()
    je.cheque_no = ((prefixe or PREFIXE_CAISSE) + description)[:140]
    je.cheque_date = je.posting_date
    je.user_remark = "\n".join(remarques)
    for ligne in lignes:
        je.append("accounts", ligne)
    je.insert(ignore_permissions=True)
    je.submit()
    return je


@frappe.whitelist()
def depenses_a_payer():
    """La file des dépenses avec facture pas encore payées (bouton « Payer »),
    chacune avec sa pièce jointe (aperçu du justificatif)."""
    frappe.only_for(ROLES)
    rows = frappe.get_all(
        "Depense A Payer",
        filters={"statut": "À payer"},
        fields=["name", "description", "montant", "tva", "fournisseur",
                "numero_facture", "date_facture", "compte_charge",
                "journal_entry", "est_bl", "numero_bl", "creation"],
        order_by="creation desc")
    if rows:
        piece = {}
        for f in frappe.get_all(
                "File",
                filters={"attached_to_doctype": "Depense A Payer",
                         "attached_to_name": ["in", [r.name for r in rows]]},
                fields=["attached_to_name", "file_url"], order_by="creation"):
            piece.setdefault(f.attached_to_name, f.file_url)
        for r in rows:
            r["piece"] = piece.get(r.name)
    return rows


@frappe.whitelist()
def depenses_bl():
    """TOUTES les dépenses facturées dont le justificatif est un BL — même
    anciennes, payées ou non. Un BL avec `numero_facture` rempli est déjà
    « facturé » (la facture reçue lui a été attachée)."""
    frappe.only_for(ROLES)
    rows = frappe.get_all(
        "Depense A Payer",
        filters={"est_bl": 1},
        fields=["name", "statut", "description", "montant", "fournisseur",
                "numero_bl", "numero_facture", "date_facture", "journal_entry",
                "reglement_journal_entry", "creation"],
        order_by="creation desc")
    if rows:
        piece = {}
        for f in frappe.get_all(
                "File",
                filters={"attached_to_doctype": "Depense A Payer",
                         "attached_to_name": ["in", [r.name for r in rows]]},
                fields=["attached_to_name", "file_url"], order_by="creation"):
            piece.setdefault(f.attached_to_name, f.file_url)
        for r in rows:
            r["piece"] = piece.get(r.name)
            r["date"] = str(r.creation)[:10]
    return rows


@frappe.whitelist()
def attacher_facture_bl(fiches, numero_facture, date_facture=None,
                        photo_facture=None, photo_facture_nom=None,
                        coins_facture=None):
    """La FACTURE reçue couvre un ou plusieurs BL de dépenses : elle s'attache
    à chaque fiche (et à son écriture), et le n° de facture est posé — le BL
    devient « facturé ». AUCUNE écriture n'est modifiée (la charge est déjà
    comptabilisée par chaque dépense)."""
    frappe.only_for(ROLES)
    fiches = json.loads(fiches) if isinstance(fiches, str) else (fiches or [])
    numero_facture = (numero_facture or "").strip()
    if not fiches:
        frappe.throw(_("Cochez au moins un BL."))
    if not numero_facture:
        frappe.throw(_("Le numéro de la facture est obligatoire."))
    if isinstance(coins_facture, str) and coins_facture.strip():
        coins_facture = json.loads(coins_facture)
    if not coins_facture:
        coins_facture = None

    faits = []
    for nom in fiches:
        f = frappe.db.get_value("Depense A Payer", nom,
                                ["est_bl", "journal_entry"], as_dict=True)
        if not f or not f.est_bl:
            continue
        frappe.db.set_value("Depense A Payer", nom,
                            {"numero_facture": numero_facture,
                             "date_facture": date_facture or None},
                            update_modified=False)
        if photo_facture:
            _attacher_scan(photo_facture, "facture-%s-%s" % (numero_facture, nom),
                           "Depense A Payer", nom, coins=coins_facture)
            if f.journal_entry and frappe.db.exists("Journal Entry", f.journal_entry):
                _attacher_scan(photo_facture, "facture-%s-%s" % (numero_facture, nom),
                               "Journal Entry", f.journal_entry, coins=coins_facture)
        frappe.get_doc("Depense A Payer", nom).add_comment(
            "Comment",
            _("🧾 Facture n° {0} reçue{1} — couvre ce BL.").format(
                numero_facture,
                _(" (du {0})").format(date_facture) if date_facture else ""))
        faits.append(nom)
    frappe.db.commit()
    return {"factures": faits}


@frappe.whitelist()
def solder_depense(fiche, date_reglement=None, mode=None, n_cheque=None,
                   banque=None, photo_cheque=None, photo_cheque_nom=None,
                   paiements=None):
    """Règle une dépense « à payer » : NOUVELLE écriture découvert → caisse/banque
    au jour du paiement — c'est ce jour-là qu'elle entre au rapport de caisse.
    Accepte le paiement fractionné (`paiements`)."""
    frappe.only_for(ROLES)
    f = frappe.get_doc("Depense A Payer", fiche)
    if f.statut != "À payer":
        frappe.throw(_("La fiche {0} est déjà payée.").format(fiche))
    regs = _paiements_normalises(f.montant, mode, n_cheque, banque,
                                 photo_cheque, photo_cheque_nom, paiements)
    if any(p["mode"] == MODE_PAS_PAYE for p in regs):
        frappe.throw(_("Le règlement d'une dette ne peut pas contenir « Pas payé »."))
    date = date_reglement or nowdate()

    # ⚠️ DEUX ÉCRITURES, JAMAIS DE MODIFICATION RÉTROACTIVE (décision
    # utilisateur 24/08 — « cela créerait des problèmes de caisse ») :
    # l'écriture de dette reste à SA date, le règlement est une écriture
    # distincte au jour du paiement (découvert → caisse/banque). Le compte de
    # découvert revient à zéro une fois la dette réglée.
    remarques = [f.description,
                 _("Type : {0}").format("Dépense avec facture (règlement)")]
    if f.fournisseur:
        remarques.append(_("Fournisseur : {0}").format(f.fournisseur))
    if f.numero_facture:
        remarques.append(_("Facture n° {0}").format(f.numero_facture))
    if f.date_facture:
        remarques.append(_("Facture du {0}, payée le {1}").format(f.date_facture, date))
    _remarques_paiements(remarques, regs)
    remarques.append(_("Règlement de la dette {0} ({1})").format(
        f.name, f.journal_entry or ""))
    remarques.append(_("Saisie caisse par {0}").format(frappe.session.user))

    # TRAÇABILITÉ : la ligne découvert du règlement RÉFÉRENCE l'écriture de
    # dette (champs standard reference_type/name — lien cliquable et
    # requêtable), et la dette reçoit un commentaire « réglée par … ».
    ligne_decouvert = {"account": COMPTE_DECOUVERT,
                       "debit_in_account_currency": flt(f.montant, 3),
                       "cost_center": CC}
    if f.journal_entry and frappe.db.exists("Journal Entry", f.journal_entry):
        ligne_decouvert["reference_type"] = "Journal Entry"
        ligne_decouvert["reference_name"] = f.journal_entry

    try:
        je = _ecriture(_lignes_credit(regs) + [ligne_decouvert],
                       f.description, remarques, date=date)
    except Exception:
        # la référence JE→JE peut être refusée selon les validations ERPNext —
        # la remarque et la fiche portent déjà le lien, on ne bloque jamais.
        ligne_decouvert.pop("reference_type", None)
        ligne_decouvert.pop("reference_name", None)
        je = _ecriture(_lignes_credit(regs) + [ligne_decouvert],
                       f.description, remarques, date=date)
    _attacher_cheques(regs, je.name)
    _copier_justificatifs(f.name, "Journal Entry", je.name,
                          source_doctype="Depense A Payer")
    if f.journal_entry and frappe.db.exists("Journal Entry", f.journal_entry):
        frappe.get_doc("Journal Entry", f.journal_entry).add_comment(
            "Comment",
            _("💸 Dette réglée le {0} par l'écriture {1} (fiche {2}).").format(
                date, frappe.utils.get_link_to_form("Journal Entry", je.name),
                frappe.utils.get_link_to_form("Depense A Payer", f.name)))
    f.db_set({"statut": "Payée", "reglement_journal_entry": je.name,
              "date_reglement": date},
             update_modified=False)
    frappe.db.commit()
    return {"name": je.name}


def _detecter_quad(img):
    """Les 4 coins du document sur l'image OpenCV, en pixels PLEINE TAILLE, ou None.

    Détection Canny + contours sur une miniature (700 px) : le plus grand
    quadrilatère franc couvrant au moins un quart de l'image."""
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    echelle = min(1.0, 700.0 / max(h, w))
    petit = cv2.resize(img, None, fx=echelle, fy=echelle) if echelle < 1 else img
    gris = cv2.cvtColor(petit, cv2.COLOR_BGR2GRAY)
    bords = cv2.Canny(cv2.GaussianBlur(gris, (5, 5), 0), 50, 150)
    bords = cv2.dilate(bords, np.ones((3, 3), np.uint8))
    contours, _rien = cv2.findContours(bords, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    aire_min = 0.25 * petit.shape[0] * petit.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        if cv2.contourArea(c) < aire_min:
            break
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype("float32") / echelle
    return None


def _redresser_document(image_bytes, coins=None):
    """Le VRAI scan : OpenCV détecte le quadrilatère de la feuille (Canny +
    contours) et REDRESSE la perspective (warpPerspective) — rendu CamScanner,
    même sur une photo prise de biais.

    `coins` : les 4 coins VALIDÉS À L'ÉCRAN (pixels pleine taille, ordre
    quelconque) — quand l'employé a ajusté le cadrage, ils font foi et la
    détection est sautée : c'est tout l'intérêt de l'aperçu (décision
    utilisateur 2026-08-20, la détection seule délimitait mal).

    Rend les octets JPEG de l'image redressée, ou None quand aucun quadrilatère
    franc ne se détache (l'appelant retombe alors sur le rognage Pillow —
    jamais de justificatif perdu). Dépendance : opencv-python-headless,
    déclarée dans le pyproject de l'app (entre dans l'image de prod au build)."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    if coins is not None:
        quad = np.array(coins, dtype="float32")
        if quad.shape != (4, 2):
            return None
    else:
        quad = _detecter_quad(img)
    if quad is None:
        return None
    somme = quad.sum(axis=1)
    diff = np.diff(quad, axis=1).ravel()
    tl, br = quad[np.argmin(somme)], quad[np.argmax(somme)]
    tr, bl = quad[np.argmin(diff)], quad[np.argmax(diff)]
    largeur = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    hauteur = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    # Des coins choisis par l'employé font foi même sur un petit document
    # (ticket, reçu) ; seule la détection automatique garde le garde-fou large.
    minimum = 80 if coins is not None else 200
    if largeur < minimum or hauteur < minimum:
        return None
    src = np.array([tl, tr, br, bl], dtype="float32")
    dst = np.array([[0, 0], [largeur - 1, 0], [largeur - 1, hauteur - 1],
                    [0, hauteur - 1]], dtype="float32")
    redresse = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst),
                                   (largeur, hauteur))
    ok, buf = cv2.imencode(".jpg", redresse, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return buf.tobytes() if ok else None


@frappe.whitelist()
def detecter_contour(photo):
    """Les 4 coins proposés pour le cadrage, à afficher sur la photo. -> dict.

    L'écran les montre en poignées déplaçables ; l'employé ajuste puis valide,
    et `creer` reçoit les coins retenus. Sans détection possible, on propose le
    plein cadre (léger retrait) : l'employé recadre lui-même."""
    frappe.only_for(ROLES)
    contenu, mimetype = _decoder(photo)
    if _est_pdf(contenu, mimetype):
        # Un PDF est déjà un document cadré : l'écran saute l'étape.
        return {"pdf": True, "largeur": 0, "hauteur": 0, "coins": [], "detecte": False}
    largeur = hauteur = 0
    quad = None
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(contenu, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            hauteur, largeur = img.shape[:2]
            quad = _detecter_quad(img)
    except ImportError:
        pass
    if not largeur:
        # Sans OpenCV, Pillow donne au moins les dimensions.
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(contenu))
        largeur, hauteur = im.size
    if quad is not None:
        coins = [[float(x), float(y)] for x, y in quad.tolist()]
        detecte = True
    else:
        rx, ry = largeur * 0.03, hauteur * 0.03
        coins = [[rx, ry], [largeur - rx, ry], [largeur - rx, hauteur - ry],
                 [rx, hauteur - ry]]
        detecte = False
    return {"largeur": largeur, "hauteur": hauteur, "coins": coins, "detecte": detecte}


def _rogner_document(img):
    """Rogne la photo au CONTOUR du document : la feuille (claire) se détache du
    fond (plus sombre) — seuillage sur miniature floutée, boîte englobante de la
    zone claire, remontée à l'échelle avec une marge.

    Sans OpenCV, donc sans correction de PERSPECTIVE : on coupe les bords, on ne
    redresse pas. Détection douteuse (zone trop petite, ou rien à couper) ->
    image d'origine, jamais un justificatif amputé."""
    from PIL import ImageFilter, ImageStat

    g = img.convert("L")
    petit = g.copy()
    petit.thumbnail((400, 400))
    flou = petit.filter(ImageFilter.GaussianBlur(3))
    seuil = ImageStat.Stat(flou).mean[0]
    boite = flou.point(lambda p: 255 if p > seuil else 0).getbbox()
    if not boite:
        return img
    sx, sy = img.width / petit.width, img.height / petit.height
    marge = 12
    l = max(0, int(boite[0] * sx) - marge)
    t = max(0, int(boite[1] * sy) - marge)
    r = min(img.width, int(boite[2] * sx) + marge)
    b = min(img.height, int(boite[3] * sy) + marge)
    aire = (r - l) * (b - t)
    if aire < 0.25 * img.width * img.height or aire > 0.96 * img.width * img.height:
        return img
    return img.crop((l, t, r, b))


def _scan_pdf(image_bytes, coins=None):
    """La photo du justificatif devient un PDF façon SCANNER : niveaux de gris,
    contraste étiré, netteté, taille bornée — lisible et léger, sans dépendance
    nouvelle (Pillow est déjà dans Frappe ; le recadrage de perspective exigerait
    OpenCV, écarté pour ne pas reconstruire l'image de prod).
    Rend les octets du PDF, ou None si l'image est illisible (on garde alors la
    photo brute)."""
    import io

    # Déjà un PDF : rien à convertir, il part tel quel.
    if _est_pdf(image_bytes):
        return image_bytes
    try:
        from PIL import Image, ImageFilter, ImageOps
        # D'abord le redressement OpenCV (perspective corrigée) ; à défaut, le
        # rognage Pillow (bords coupés, pas de redressement).
        redresse = _redresser_document(image_bytes, coins=coins)
        img = Image.open(io.BytesIO(redresse or image_bytes))
        if not redresse:
            img = ImageOps.exif_transpose(img)      # la photo de téléphone arrive tournée
            img = _rogner_document(img)             # ne garder que le document
        if max(img.size) > 2200:
            img.thumbnail((2200, 2200))
        img = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        img = img.filter(ImageFilter.SHARPEN)
        sortie = io.BytesIO()
        img.save(sortie, format="PDF", resolution=150.0)
        return sortie.getvalue()
    except Exception:
        return None


def _attacher_scan(photo, nom_base, doctype, name, coins=None):
    """Attache le justificatif en PDF scanné ; repli sur la photo brute si la
    conversion échoue. `coins` : le cadrage validé à l'écran, prioritaire."""
    from frappe.utils.file_manager import save_file

    contenu, _mt = _decoder(photo)
    pdf = _scan_pdf(contenu, coins=coins)
    if pdf:
        save_file("%s.pdf" % nom_base, pdf, doctype, name, is_private=1)
    else:
        save_file("%s.jpg" % nom_base, contenu, doctype, name, is_private=1)


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


# ------------------------------------------------------------------ rattachement des
# vraies Purchase Invoice aux fiches de caisse (hooks Purchase Invoice)

def _copier_justificatifs(fiche_nom, doctype, name,
                          source_doctype="Facture Achat a Saisir"):
    """Fait suivre le justificatif scanné en caisse sur une pièce comptable.

    Idempotent : un fichier déjà présent (même URL) n'est pas recopié — sinon
    chaque enregistrement de la facture empilerait une pièce jointe de plus."""
    for f in frappe.get_all("File",
                            filters={"attached_to_doctype": source_doctype,
                                     "attached_to_name": fiche_nom},
                            fields=["file_url", "file_name", "is_private"]):
        if not f.file_url or frappe.db.exists("File", {
                "attached_to_doctype": doctype, "attached_to_name": name,
                "file_url": f.file_url}):
            continue
        frappe.get_doc({"doctype": "File", "file_url": f.file_url,
                        "file_name": f.file_name, "is_private": f.is_private,
                        "attached_to_doctype": doctype,
                        "attached_to_name": name}).insert(ignore_permissions=True)


def _fiche_de(doc):
    """La fiche de caisse de cette facture d'achat, ou None.

    D'abord le lien déjà posé, sinon l'appariement (fournisseur, n° de facture)
    sur une fiche encore « À saisir » — c'est le n° que le comptable recopie du
    justificatif, la clé naturelle des deux côtés."""
    # est_bl=0 : ne jamais confondre la fiche « facture » avec un BL déjà
    # rattaché à la main — les deux peuvent pointer la même facture.
    nom = frappe.db.get_value(
        "Facture Achat a Saisir",
        {"purchase_invoice": doc.name, "est_bl": 0}, "name")
    if nom:
        return nom
    numero = (doc.get("bill_no") or "").strip()
    if not (doc.get("supplier") and numero):
        return None
    return frappe.db.get_value(
        "Facture Achat a Saisir",
        {"supplier": doc.supplier, "numero_facture": numero, "statut": "À saisir",
         "est_bl": 0, "purchase_invoice": ["is", "not set"]}, "name")


def _fiches_de(doc):
    """TOUTES les fiches de caisse rattachées à cette facture — la fiche
    « facture » appariée par n°, plus les BL rattachés à la main."""
    return frappe.get_all("Facture Achat a Saisir",
                          filters={"purchase_invoice": doc.name}, pluck="name")


def _pieces_fas(rows):
    """Complète chaque fiche FAS d'une liste avec sa 1re pièce jointe."""
    if not rows:
        return rows
    piece = {}
    for f in frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Facture Achat a Saisir",
                     "attached_to_name": ["in", [r.name for r in rows]]},
            fields=["attached_to_name", "file_url"], order_by="creation"):
        piece.setdefault(f.attached_to_name, f.file_url)
    for r in rows:
        r["piece"] = piece.get(r.name)
        r["date"] = str(r.creation)[:10]
    return rows


#: Période de suivi des achats (décision utilisateur 24/08) : rien d'antérieur.
DATE_SUIVI_ACHATS = "2026-01-01"


@frappe.whitelist()
def factures_a_payer():
    """Deux groupes (décision utilisateur 24/08) :
    - TOUTES les factures d'achat soumises pas totalement payées (encours > 0),
      sans plancher de date, chacune avec sa pièce jointe ;
    - les CAPTURES de caisse « Pas payé » / « Mixte » encore à saisir."""
    frappe.only_for(ROLES)
    factures = frappe.db.sql("""
        SELECT name, supplier, posting_date, bill_no, due_date,
               grand_total, rounded_total, outstanding_amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1 AND outstanding_amount > 0.001
        ORDER BY posting_date DESC""", as_dict=True)
    piece = {}
    if factures:
        for f in frappe.get_all(
                "File",
                filters={"attached_to_doctype": "Purchase Invoice",
                         "attached_to_name": ["in", [x.name for x in factures]]},
                fields=["attached_to_name", "file_url"], order_by="creation"):
            piece.setdefault(f.attached_to_name, f.file_url)
    for f in factures:
        f["montant"] = flt(f.rounded_total or f.grand_total, 3)
        f["piece"] = piece.get(f.name)
    fiches = _pieces_fas(frappe.get_all(
        "Facture Achat a Saisir",
        filters={"statut": "À saisir",
                 "mode_paiement": ["in", ["Pas payé", "Mixte"]]},
        fields=["name", "statut", "fournisseur", "supplier", "montant",
                "numero_facture", "date_facture", "mode_paiement", "est_bl",
                "numero_bl", "journal_entry", "purchase_invoice", "creation"],
        order_by="creation desc"))
    return {"factures": factures, "fiches": fiches}


@frappe.whitelist()
def factures_sans_justificatif():
    """Les factures d'achat soumises depuis le 01-01-2026 SANS AUCUNE pièce
    jointe — la preuve scannée manque, à compléter."""
    frappe.only_for(ROLES)
    return frappe.db.sql("""
        SELECT pi.name, pi.supplier, pi.posting_date, pi.bill_no,
               pi.grand_total, pi.rounded_total, pi.outstanding_amount
        FROM `tabPurchase Invoice` pi
        WHERE pi.docstatus = 1 AND pi.posting_date >= %s
          AND NOT EXISTS (SELECT 1 FROM `tabFile` f
                          WHERE f.attached_to_doctype = 'Purchase Invoice'
                            AND f.attached_to_name = pi.name)
        ORDER BY pi.posting_date DESC""", DATE_SUIVI_ACHATS, as_dict=True)


@frappe.whitelist()
def factures_bl():
    """TOUS les achats capturés sur bon de livraison (fiches est_bl), quel que
    soit leur statut — pour la fusion en facture globale."""
    frappe.only_for(ROLES)
    rows = _pieces_fas(frappe.get_all(
        "Facture Achat a Saisir",
        filters={"est_bl": 1},
        fields=["name", "statut", "fournisseur", "supplier", "montant",
                "numero_facture", "date_facture", "mode_paiement",
                "numero_bl", "journal_entry", "journal_entries",
                "purchase_order", "purchase_receipt", "purchase_invoice",
                "creation"],
        order_by="creation desc"))
    # la facture ne peut naître que de commandes SOUMISES : le dialogue ne
    # rend cochables que celles-là (brouillon = badge distinct).
    pos = [r.purchase_order for r in rows if r.purchase_order]
    docstatus = {}
    if pos:
        docstatus = {p.name: p.docstatus for p in frappe.get_all(
            "Purchase Order", filters={"name": ["in", pos]},
            fields=["name", "docstatus"])}
    for r in rows:
        r["po_docstatus"] = docstatus.get(r.purchase_order)
    return rows


# --------------------------------------------- BL -> reçu d'achat -> facture
#
# Décision utilisateur 24/08 : un achat SANS facture (BL) devient un REÇU
# D'ACHAT ERPNext (le comptable y saisit les articles depuis la photo — le
# stock entre à la réception), et la facture d'achat se crée nativement depuis
# UN OU PLUSIEURS reçus (« Obtenir les articles depuis > Reçu d'achat »).
# Un paiement ne peut PAS référencer un reçu (pas d'encours) : l'avance de
# caisse reste sur la fiche BL, et devient un paiement DE LA FACTURE à sa
# soumission (mécanisme _remplacer_avance_par_paiement).


def pr_lier_fiche_caisse(doc, method=None):
    """Purchase Receipt on_update/on_submit : le reçu créé depuis une fiche BL
    (champ custom_fiche_caisse, posé par la page Caisse) se lie à sa fiche et
    reçoit son justificatif."""
    nom = doc.get("custom_fiche_caisse")
    if not nom or not frappe.db.exists("Facture Achat a Saisir", nom):
        return
    frappe.db.set_value("Facture Achat a Saisir", nom,
                        "purchase_receipt", doc.name, update_modified=False)
    _copier_justificatifs(nom, "Purchase Receipt", doc.name)


def pr_detacher_fiche_caisse(doc, method=None):
    """Purchase Receipt on_cancel : la fiche BL redevient sans reçu."""
    nom = frappe.db.get_value("Facture Achat a Saisir",
                              {"purchase_receipt": doc.name}, "name")
    if nom:
        frappe.db.set_value("Facture Achat a Saisir", nom,
                            "purchase_receipt", "", update_modified=False)


def po_lier_fiche_caisse(doc, method=None):
    """Purchase Order on_update : la commande créée depuis une fiche BL
    (custom_fiche_caisse) se lie à sa fiche et reçoit son justificatif."""
    nom = doc.get("custom_fiche_caisse")
    if not nom or not frappe.db.exists("Facture Achat a Saisir", nom):
        return
    frappe.db.set_value("Facture Achat a Saisir", nom,
                        "purchase_order", doc.name, update_modified=False)
    _copier_justificatifs(nom, "Purchase Order", doc.name)


def po_convertir_avances(doc, method=None):
    """Purchase Order on_submit : l'avance de caisse du BL devient un PAIEMENT
    LIÉ À LA COMMANDE (avance fournisseur native) — « comme pour la facture
    d'achat », décision utilisateur 24/08. À la facture (créée depuis une ou
    plusieurs commandes), ces avances s'allouent automatiquement. Le REÇU
    d'achat est généré dans la foulée (la marchandise du BL est déjà livrée)."""
    po_lier_fiche_caisse(doc)
    total = flt(doc.get("rounded_total") or doc.grand_total, 3)
    for nom in frappe.get_all("Facture Achat a Saisir",
                              filters={"purchase_order": doc.name}, pluck="name"):
        deja = flt(frappe.db.sql("""
            select sum(allocated_amount) from `tabPayment Entry Reference`
            where reference_doctype = 'Purchase Order' and reference_name = %s
              and docstatus = 1""", doc.name)[0][0], 3)
        _avances_en_paiements(nom, "Purchase Order", doc.name,
                              total, deja, doc.company or COMPANY)
    _po_recu_auto(doc)


def _po_recu_auto(doc):
    """Génère et soumet le reçu d'achat d'une commande née du pipeline caisse
    (décision utilisateur 24/08) : le BL prouve que la marchandise est LIVRÉE —
    le stock entre à la soumission de la commande, pas à la facture. Un échec
    ne bloque jamais la commande (avertissement + journal)."""
    nom = doc.get("custom_fiche_caisse")
    if not nom or not frappe.db.exists("Facture Achat a Saisir", nom):
        return
    if frappe.db.get_value("Facture Achat a Saisir", nom, "purchase_receipt"):
        return
    try:
        from erpnext.buying.doctype.purchase_order.purchase_order import (
            make_purchase_receipt)
        pr = make_purchase_receipt(doc.name)
        pr.custom_fiche_caisse = nom
        pr.flags.ignore_permissions = True
        pr.insert()
        pr.submit()   # les hooks PR lient la fiche + copient le justificatif
        frappe.msgprint(_("📦 Reçu d'achat {0} généré et soumis — le stock est "
                          "entré.").format(pr.name), alert=True, indicator="green")
    except Exception:
        frappe.log_error(title="Caisse: échec reçu auto",
                         message=frappe.get_traceback())
        frappe.msgprint(_("⚠️ Le reçu d'achat n'a pas pu être généré "
                          "automatiquement — créez-le depuis la commande "
                          "(Créer > Reçu d'achat)."), indicator="orange")


def po_detacher_fiche_caisse(doc, method=None):
    """Purchase Order on_cancel : la fiche encore « À saisir » se détache.
    Les paiements d'avance déjà créés restent (l'argent est sorti) — ils
    redeviennent des avances fournisseur non allouées."""
    for nom in frappe.get_all("Facture Achat a Saisir",
                              filters={"purchase_order": doc.name,
                                       "statut": "À saisir"}, pluck="name"):
        frappe.db.set_value("Facture Achat a Saisir", nom,
                            "purchase_order", "", update_modified=False)


@frappe.whitelist()
def creer_facture_depuis_commandes(fiches):
    """UNE facture d'achat (brouillon) née de PLUSIEURS commandes BL du même
    fournisseur — sélection faite dans « 📦 Achats BL » de la page Caisse. Les
    avances liées aux commandes sont tirées dans la facture (allocation à la
    soumission). Le comptable complète (n° facture fournisseur…) puis soumet."""
    frappe.only_for(ROLES)
    fiches = json.loads(fiches) if isinstance(fiches, str) else (fiches or [])
    pos = []
    for nom in fiches:
        po = frappe.db.get_value("Facture Achat a Saisir", nom, "purchase_order")
        if not po:
            frappe.throw(_("La fiche {0} n'a pas encore de commande d'achat.").format(nom))
        if frappe.db.get_value("Purchase Order", po, "docstatus") != 1:
            frappe.throw(_("La commande {0} doit être soumise d'abord.").format(po))
        if po not in pos:
            pos.append(po)
    if not pos:
        frappe.throw(_("Cochez au moins un BL avec commande."))
    suppliers = {frappe.db.get_value("Purchase Order", po, "supplier") for po in pos}
    if len(suppliers) > 1:
        frappe.throw(_("Toutes les commandes doivent être du même fournisseur."))

    from erpnext.buying.doctype.purchase_order.purchase_order import (
        make_purchase_invoice as pi_depuis_commande)
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
        make_purchase_invoice as pi_depuis_recu)
    # Quand le reçu d'achat existe (généré automatiquement à la soumission de
    # la commande), la facture se construit DEPUIS LE REÇU : le stock est déjà
    # entré, la garde BRS laissera update_stock décoché. Sinon, depuis la
    # commande (le stock entrera à la facture).
    pi = None
    for po in pos:
        pr = frappe.db.get_value(
            "Facture Achat a Saisir",
            {"purchase_order": po, "purchase_receipt": ["is", "set"]},
            "purchase_receipt")
        if pr and frappe.db.get_value("Purchase Receipt", pr, "docstatus") == 1:
            pi = pi_depuis_recu(pr, target_doc=pi)
        else:
            pi = pi_depuis_commande(po, target_doc=pi)
    # update_stock : la politique BRS (corriger_stock) tranche — coché quand la
    # facture entre la marchandise (pas de reçu), décoché si des reçus existent.
    pi.run_method("set_missing_values")
    pi.calculate_taxes_and_totals()
    # échéancier obligatoire sur ce site (Payment Schedule.mode_of_payment reqd)
    pi.set("payment_schedule", [])
    pi.append("payment_schedule", {
        "due_date": pi.due_date or nowdate(), "invoice_portion": 100,
        "payment_amount": flt(pi.rounded_total or pi.grand_total, 3),
        "mode_of_payment": "Espèces"})
    try:
        pi.set_advances()   # tire les avances liées aux commandes
    except Exception:
        pass
    pi.flags.ignore_permissions = True
    pi.insert()
    frappe.db.commit()
    return {"purchase_invoice": pi.name, "commandes": pos}


@frappe.whitelist()
def bls_en_attente(supplier):
    """Les BL de ce fournisseur encore en attente d'une facture — pour le bouton
    « Rattacher des BL » du formulaire Purchase Invoice."""
    frappe.only_for(ROLES)
    if not supplier:
        return []
    return frappe.get_all(
        "Facture Achat a Saisir",
        filters={"supplier": supplier, "est_bl": 1, "statut": "À saisir",
                 "purchase_invoice": ["is", "not set"]},
        fields=["name", "numero_bl", "montant", "date_facture", "description",
                "mode_paiement", "creation"],
        order_by="creation asc")


@frappe.whitelist()
def rattacher_bls(purchase_invoice, fiches):
    """Rattache des fiches BL (sélection manuelle) à une facture d'achat en
    BROUILLON. À la soumission de la facture, chaque fiche passera « Saisie » et
    chaque avance deviendra un paiement référençant la facture."""
    frappe.only_for(ROLES)
    fiches = json.loads(fiches) if isinstance(fiches, str) else (fiches or [])
    doc = frappe.get_doc("Purchase Invoice", purchase_invoice)
    if doc.docstatus != 0:
        frappe.throw(_("Le rattachement de BL se fait sur une facture en brouillon."))
    lies = []
    for nom in fiches:
        f = frappe.db.get_value(
            "Facture Achat a Saisir", nom,
            ["supplier", "statut", "purchase_invoice", "est_bl"], as_dict=True)
        if not f or not f.est_bl or f.statut != "À saisir" or f.purchase_invoice:
            continue
        if f.supplier != doc.supplier:
            frappe.throw(_("Le BL {0} appartient à un autre fournisseur.").format(nom))
        frappe.db.set_value("Facture Achat a Saisir", nom,
                            "purchase_invoice", doc.name, update_modified=False)
        _copier_justificatifs(nom, "Purchase Invoice", doc.name)
        lies.append(nom)
    frappe.db.commit()
    return {"rattaches": lies}


def pi_lier_fiche_caisse(doc, method=None):
    """Purchase Invoice on_update / on_submit : rattache la facture à sa fiche de
    caisse et fait SUIVRE LE JUSTIFICATIF capturé (le scan de la caisse est la
    preuve de la facture — décision utilisateur 2026-08-20). Une facture sans
    fiche passe sans bruit : toutes les factures d'achat ne viennent pas de la
    caisse.

    ⚠️ APRÈS L'ENREGISTREMENT, JAMAIS AU `validate`. Au validate la facture
    n'existe pas encore : une insertion qui échoue plus loin (un champ
    obligatoire manquant, par exemple) laissait la fiche pointer vers un numéro
    de facture qui n'a jamais été créé — le même piège que les justificatifs
    fantômes vus le 20/08/2026."""
    nom = _fiche_de(doc)
    if nom:
        frappe.db.set_value("Facture Achat a Saisir", nom, "purchase_invoice", doc.name,
                            update_modified=False)
        _copier_justificatifs(nom, "Purchase Invoice", doc.name)
    # les fiches BL dont le REÇU D'ACHAT alimente cette facture (« Obtenir les
    # articles depuis > Reçu d'achat ») se rattachent aussi : leur avance
    # deviendra un paiement de la facture à la soumission.
    prs = {it.purchase_receipt for it in (doc.get("items") or [])
           if it.get("purchase_receipt")}
    pos = {it.purchase_order for it in (doc.get("items") or [])
           if it.get("purchase_order")}
    for champ, refs in (("purchase_receipt", prs), ("purchase_order", pos)):
        if not refs:
            continue
        for fr in frappe.get_all(
                "Facture Achat a Saisir",
                filters={champ: ["in", list(refs)],
                         "statut": "À saisir",
                         "purchase_invoice": ["is", "not set"]},
                pluck="name"):
            frappe.db.set_value("Facture Achat a Saisir", fr,
                                "purchase_invoice", doc.name, update_modified=False)
            _copier_justificatifs(fr, "Purchase Invoice", doc.name)


def _remplacer_avance_par_paiement(doc, fiche_nom):
    """L'avance de la fiche devient le(s) paiement(s) de CETTE facture."""
    total = flt(doc.get("rounded_total") or doc.grand_total, 3)
    deja = flt(frappe.db.sql("""
        select sum(allocated_amount) from `tabPayment Entry Reference`
        where reference_doctype = 'Purchase Invoice' and reference_name = %s
          and docstatus = 1""", doc.name)[0][0], 3)
    return _avances_en_paiements(fiche_nom, "Purchase Invoice", doc.name,
                                 total, deja, doc.company or COMPANY)


def _avances_en_paiements(fiche_nom, ref_dt, ref_name, total, deja, company):
    """Les écritures d'avance de la fiche deviennent de VRAIS paiements
    référençant `ref_name` — une FACTURE d'achat, ou une COMMANDE d'achat
    (avance fournisseur native, décision utilisateur 24/08).

    ⚠️ POURQUOI REMPLACER PLUTÔT QU'AJOUTER (décision utilisateur 2026-08-20).
    L'écriture posée en caisse (Cr Espèces ou banque / Dr Créditeurs) constate la
    sortie d'argent mais ne se rattache à rien : la facture d'achat saisie plus
    tard restait « impayée » et l'avance « non allouée », les deux se regardant
    au solde du fournisseur sans jamais se solder. Un Payment Entry, lui, PORTE
    la référence de la facture : il l'éteint. On détruit donc l'écriture — même
    montant, même date, même compte, même mode — et on crée le paiement.

    ⚠️ ATOMIQUE PAR CONSTRUCTION : aucun commit ici. Tout se joue dans la
    transaction de soumission de la facture — si la création du paiement échoue,
    la destruction de l'écriture est annulée avec la soumission elle-même.
    """
    f = frappe.db.get_value("Facture Achat a Saisir", fiche_nom,
                            ["journal_entry", "journal_entries", "supplier",
                             "mode_paiement"], as_dict=True)
    jes = []
    if f.journal_entry:
        jes.append(f.journal_entry)
    try:
        jes += [x for x in (json.loads(f.journal_entries or "[]") or [])
                if x not in jes]
    except Exception:
        pass
    jes = [j for j in jes if frappe.db.exists("Journal Entry", j)]
    supplier = f.supplier
    if not (jes and supplier):
        return None

    # ⚠️ CE QUE LA PIÈCE DOIT ENCORE, CALCULÉ, PAS RELU (`total` − `deja`
    # fournis par l'appelant) : à la soumission `outstanding_amount` n'est pas
    # encore posé — s'y fier donnait 0 et ERPNext refusait le paiement.
    restant = max(0.0, flt(total - deja, 3))

    pes = []
    # PLUSIEURS écritures d'avance possibles (fiche née de plusieurs BL) ; dans
    # chaque écriture, PLUSIEURS lignes créditées possibles (paiement
    # fractionné) : un Payment Entry par ligne, chacun sur son compte.
    a_traiter = []
    for je_nom in jes:
        je = frappe.get_doc("Journal Entry", je_nom)
        if je.docstatus == 2:
            continue
        credits = [(l.account, flt(l.credit_in_account_currency, 3))
                   for l in je.accounts if flt(l.credit_in_account_currency) > 0]
        if not credits:
            continue
        date = je.posting_date
        mode = je.get("mode_of_payment") or f.mode_paiement
        reference = je.get("cheque_no") or ""
        remarque = je.get("user_remark") or ""
        je.flags.ignore_permissions = True
        je.flags.ignore_links = True
        je.cancel()
        frappe.delete_doc("Journal Entry", je_nom, ignore_permissions=True, force=True)
        a_traiter += [(compte, montant, date, mode, reference, remarque)
                      for compte, montant in credits]

    for compte_paiement, montant, date, mode, reference, remarque in a_traiter:
        if montant <= 0:
            continue
        alloue = min(montant, restant)
        restant = flt(restant - alloue, 3)
        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Pay"
        pe.party_type = "Supplier"
        pe.party = supplier
        pe.company = company or COMPANY
        pe.posting_date = date
        # ⚠️ Payment Entry.mode_of_payment est OBLIGATOIRE sur ce site (property
        # setter). Ligne espèces → Espèces ; ligne banque → le mode de la fiche
        # s'il est bancaire, sinon Chèque si l'écriture en portait un, sinon carte.
        if compte_paiement == COMPTE_ESPECES:
            pe.mode_of_payment = "Espèces"
        elif mode in ("Chèque", "Carte de crédit"):
            pe.mode_of_payment = mode
        else:
            pe.mode_of_payment = "Chèque" if "Chq N°" in (remarque or "") else "Carte de crédit"
        pe.paid_from = compte_paiement
        pe.paid_to = COMPTE_CREDITEURS
        pe.paid_amount = montant
        pe.received_amount = montant
        pe.source_exchange_rate = 1
        pe.target_exchange_rate = 1
        pe.reference_no = reference or ref_name
        pe.reference_date = date
        pe.remarks = remarque or reference
        # Une pièce déjà soldée par ailleurs ne peut rien recevoir : le paiement
        # existe quand même (l'argent est sorti), mais il reste non alloué sur le
        # compte du fournisseur au lieu de faire échouer la soumission.
        if alloue > 0.001:
            pe.append("references", {"reference_doctype": ref_dt,
                                     "reference_name": ref_name,
                                     "allocated_amount": alloue})
        pe.flags.ignore_permissions = True
        pe.insert()
        pe.submit()
        pes.append(pe.name)

    if not pes:
        return None
    frappe.db.set_value("Facture Achat a Saisir", fiche_nom,
                        {"payment_entry": pes[0],
                         "payment_entries": json.dumps(pes),
                         "journal_entry": "", "journal_entries": ""},
                        update_modified=False)
    for pe_nom in pes:
        _copier_justificatifs(fiche_nom, "Payment Entry", pe_nom)
    return pes[0]


def pi_marquer_fiche_saisie(doc, method=None):
    """Purchase Invoice on_submit : la fiche de caisse est comptabilisée, et
    l'écriture d'avance de la caisse devient le paiement de CETTE facture.

    Le rattachement est refait ici : une facture créée ET soumise d'un trait ne
    passe pas par `on_update`."""
    pi_lier_fiche_caisse(doc)
    # TOUTES les fiches rattachées — la fiche « facture » ET les BL couverts par
    # cette facture : chacune passe « Saisie », chaque avance devient un paiement.
    for nom in _fiches_de(doc):
        frappe.db.set_value("Facture Achat a Saisir", nom, "statut", "Saisie",
                            update_modified=False)
        _remplacer_avance_par_paiement(doc, nom)


def pi_rouvrir_fiche(doc, method=None):
    """Purchase Invoice on_cancel : la fiche repart dans la file « À saisir ».

    ⚠️ LE PAIEMENT CRÉÉ À LA SOUMISSION EST ANNULÉ AVEC ELLE. Il ne référence
    qu'elle : le laisser vivant laisserait un règlement rattaché à une facture
    annulée, et l'argent sorti sans contrepartie. La fiche garde sa trace (le
    paiement annulé reste consultable) et repart dans la file."""
    for nom in _fiches_de(doc):
        f = frappe.db.get_value("Facture Achat a Saisir", nom,
                                ["payment_entry", "payment_entries"], as_dict=True)
        pes = []
        try:
            pes = json.loads(f.payment_entries or "[]") or []
        except Exception:
            pass
        if f.payment_entry and f.payment_entry not in pes:
            pes.append(f.payment_entry)
        for pe_nom in pes:
            if pe_nom and frappe.db.exists("Payment Entry", pe_nom):
                pe = frappe.get_doc("Payment Entry", pe_nom)
                if pe.docstatus == 1:
                    pe.flags.ignore_permissions = True
                    pe.cancel()
        frappe.db.set_value("Facture Achat a Saisir", nom,
                            {"statut": "À saisir", "purchase_invoice": ""},
                            update_modified=False)


# ---------------------------------------------------------------- voir le document à saisir

#: Les pièces qu'on sait afficher côté navigateur. Un .docx ne s'ouvre pas dans un onglet.
_AFFICHABLES = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif")

#: Où la fiche de la file range le document, selon la pièce que le comptable est en train de
#: saisir. C'est le seul chemin de retour : la fiche pointe la pièce, jamais l'inverse.
_CHAMP_FICHE = {
    "Purchase Invoice": "purchase_invoice",
    "Purchase Order": "purchase_order",
    "Purchase Receipt": "purchase_receipt",
}


@frappe.whitelist()
def scans_a_saisir(doctype, name=None, fiche=None, bill_no=None, supplier=None):
    """Les scans à consulter pendant la saisie d'une facture ou d'une commande d'achat.

    ⚠️ LE SCAN N'EST PAS SUR LA PIÈCE QU'ON SAISIT. Il a été pris en caisse et attaché à la
    fiche de la file (« Facture Achat a Saisir ») ; la facture d'achat, elle, naît vide. Sans ce
    détour, le bouton n'aurait rien à montrer au moment précis où on en a besoin — la fiche
    pointe la pièce, jamais l'inverse, d'où la recherche à l'envers.
    """
    frappe.only_for(ROLES)
    vus, out = set(), []

    def ajouter(dt, dn, origine):
        for f in frappe.get_all("File",
                                filters={"attached_to_doctype": dt, "attached_to_name": dn},
                                fields=["name", "file_name", "file_url"], order_by="creation"):
            nom = (f.file_name or "").lower()
            if not nom.endswith(_AFFICHABLES) or f.file_url in vus:
                continue
            vus.add(f.file_url)
            out.append({"nom": f.file_name, "url": f.file_url, "origine": origine,
                        "pdf": nom.endswith(".pdf")})

    if name:
        ajouter(doctype, name, _("pièce en cours"))

    fiches = []
    # 1. La fiche que la pièce désigne (le bouton « Créer la commande d'achat » la pose dans
    #    `custom_fiche_caisse`, et elle vaut même avant enregistrement).
    if fiche and frappe.db.exists("Facture Achat a Saisir", fiche):
        fiches.append(fiche)
    # 2. La fiche qui désigne la pièce, une fois celle-ci enregistrée et appariée.
    champ = _CHAMP_FICHE.get(doctype)
    if champ and name:
        fiches += frappe.get_all("Facture Achat a Saisir", filters={champ: name}, pluck="name")
    # 3. ⚠️ ET LE CAS QUI COMPTE VRAIMENT : LA FACTURE PAS ENCORE ENREGISTRÉE. Le bouton
    #    « Créer la facture d'achat » ne transmet que fournisseur, n° et date — aucun lien. Or
    #    c'est PENDANT la frappe qu'on a besoin de voir le scan, pas après. On retrouve donc la
    #    fiche par son n° de facture, celui-là même que le bouton vient de préremplir.
    if not fiches and (bill_no or "").strip():
        filtres = {"numero_facture": (bill_no or "").strip(), "statut": "À saisir"}
        if supplier:
            filtres["supplier"] = supplier
        fiches += frappe.get_all("Facture Achat a Saisir", filters=filtres, pluck="name")

    for nom in dict.fromkeys(fiches):
        ajouter("Facture Achat a Saisir", nom, _("file d'attente {0}").format(nom))
    return {"scans": out}
