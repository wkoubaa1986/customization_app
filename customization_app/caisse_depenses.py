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
            "(le fournisseur), tel qu'écrit ; null si absent>}. "
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
    except Exception:
        return None, "", ""
    return (compte if compte in comptes else None), description, matricule


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
            "Tu lis la facture ou le reçu photographié d'une dépense d'entreprise "
            "tunisienne. Réponds STRICTEMENT en JSON : "
            "{\"description\": <ce qui a été acheté, en français, 3 à 10 mots, "
            "sans montant ni date>, "
            "\"matricule\": <le matricule fiscal de l'ÉMETTEUR de la facture "
            "(le fournisseur), tel qu'écrit ; null si absent>}. "
            + _consigne_matricule()),
        input=[{"role": "user", "content": [
            _bloc_document(image_bytes, mimetype),
            {"type": "input_text", "text": "Décris la dépense et lis le matricule."}]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    try:
        lu = json.loads(texte)
        return ((lu.get("description") or "").strip(),
                (lu.get("matricule") or "").strip())
    except Exception:
        return "", ""


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
    }
    if type_depense == "Dépense avec facture":
        try:
            out["compte_suggere"], out["description"], out["matricule"] = _classifier(
                contenu, mimetype, d)
        except Exception:
            out["compte_suggere"] = None   # la classification est une aide, jamais un blocage
    else:
        # Pas de classification pour une facture d'achat, mais la description lue
        # sur la photo sert autant (décision utilisateur 2026-08-20).
        try:
            out["description"], out["matricule"] = _decrire(contenu, mimetype)
        except Exception:
            pass
    # ⚠️ FOURNISSEUR ET N° DE FACTURE TOUJOURS DANS LA DESCRIPTION (décision
    # utilisateur 2026-08-20) : elle devient le « N° de référence » de l'écriture
    # de journal (« Dépense caisse — … ») — c'est par elle qu'on retrouve la pièce.
    morceaux = [m for m in (out["description"], out["fournisseur"]) if m]
    if out["numero"]:
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


@frappe.whitelist()
def creer(type_depense, montant, mode, compte=None, description=None, fournisseur=None,
          tva=0, taux_tva=0, numero_facture=None, date_facture=None,
          n_cheque=None, banque=None, photo_facture=None, photo_facture_nom=None,
          photo_cheque=None, photo_cheque_nom=None, coins_facture=None,
          supplier=None, matricule=None):
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
        supplier = _supplier(fournisseur, matricule=matricule, supplier=supplier)
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
            _attacher_scan(photo_facture, "facture-%s" % je.name,
                           "Journal Entry", je.name, coins=coins_facture)
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

def _copier_justificatifs(fiche_nom, doctype, name):
    """Fait suivre le justificatif scanné en caisse sur une pièce comptable.

    Idempotent : un fichier déjà présent (même URL) n'est pas recopié — sinon
    chaque enregistrement de la facture empilerait une pièce jointe de plus."""
    for f in frappe.get_all("File",
                            filters={"attached_to_doctype": "Facture Achat a Saisir",
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
    nom = frappe.db.get_value("Facture Achat a Saisir", {"purchase_invoice": doc.name}, "name")
    if nom:
        return nom
    numero = (doc.get("bill_no") or "").strip()
    if not (doc.get("supplier") and numero):
        return None
    return frappe.db.get_value(
        "Facture Achat a Saisir",
        {"supplier": doc.supplier, "numero_facture": numero, "statut": "À saisir",
         "purchase_invoice": ["is", "not set"]}, "name")


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
    if not nom:
        return
    frappe.db.set_value("Facture Achat a Saisir", nom, "purchase_invoice", doc.name,
                        update_modified=False)
    _copier_justificatifs(nom, "Purchase Invoice", doc.name)


def _remplacer_avance_par_paiement(doc, fiche_nom):
    """L'écriture d'avance de la caisse devient un VRAI paiement de la facture.

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
    je_nom = frappe.db.get_value("Facture Achat a Saisir", fiche_nom, "journal_entry")
    if not je_nom or not frappe.db.exists("Journal Entry", je_nom):
        return None
    je = frappe.get_doc("Journal Entry", je_nom)
    if je.docstatus == 2:
        return None

    # Le compte d'où l'argent est sorti : la ligne CRÉDITÉE de l'écriture.
    compte_paiement = next((l.account for l in je.accounts
                            if flt(l.credit_in_account_currency) > 0), None)
    montant = flt(je.total_debit, 3)
    supplier = frappe.db.get_value("Facture Achat a Saisir", fiche_nom, "supplier")
    if not (compte_paiement and montant > 0 and supplier):
        return None

    date = je.posting_date
    mode = je.get("mode_of_payment") or frappe.db.get_value(
        "Facture Achat a Saisir", fiche_nom, "mode_paiement")
    reference = je.get("cheque_no") or ""
    remarque = je.get("user_remark") or ""

    je.flags.ignore_permissions = True
    je.flags.ignore_links = True
    je.cancel()
    frappe.delete_doc("Journal Entry", je_nom, ignore_permissions=True, force=True)

    # ⚠️ CE QUE LA FACTURE DOIT ENCORE, CALCULÉ, PAS RELU. À la soumission
    # `outstanding_amount` n'est pas encore posé (il l'est avec les écritures) :
    # s'y fier donnait 0, et ERPNext refusait le paiement en criant que la
    # facture était déjà réglée. On additionne donc ce que les paiements soumis
    # lui ont déjà alloué et on retranche.
    total = flt(doc.get("rounded_total") or doc.grand_total, 3)
    deja = flt(frappe.db.sql("""
        select sum(allocated_amount) from `tabPayment Entry Reference`
        where reference_doctype = 'Purchase Invoice' and reference_name = %s
          and docstatus = 1""", doc.name)[0][0], 3)
    alloue = min(montant, max(0.0, flt(total - deja, 3)))

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Pay"
    pe.party_type = "Supplier"
    pe.party = supplier
    pe.company = doc.company or COMPANY
    pe.posting_date = date
    pe.mode_of_payment = mode or None
    pe.paid_from = compte_paiement
    pe.paid_to = COMPTE_CREDITEURS
    pe.paid_amount = montant
    pe.received_amount = montant
    pe.source_exchange_rate = 1
    pe.target_exchange_rate = 1
    pe.reference_no = reference or doc.name
    pe.reference_date = date
    pe.remarks = remarque or reference
    # Une facture déjà soldée par ailleurs ne peut rien recevoir : le paiement
    # existe quand même (l'argent est sorti), mais il reste non alloué sur le
    # compte du fournisseur au lieu de faire échouer la soumission.
    if alloue > 0.001:
        pe.append("references", {"reference_doctype": "Purchase Invoice",
                                 "reference_name": doc.name,
                                 "allocated_amount": alloue})
    pe.flags.ignore_permissions = True
    pe.insert()
    pe.submit()

    frappe.db.set_value("Facture Achat a Saisir", fiche_nom,
                        {"payment_entry": pe.name, "journal_entry": ""},
                        update_modified=False)
    _copier_justificatifs(fiche_nom, "Payment Entry", pe.name)
    return pe.name


def pi_marquer_fiche_saisie(doc, method=None):
    """Purchase Invoice on_submit : la fiche de caisse est comptabilisée, et
    l'écriture d'avance de la caisse devient le paiement de CETTE facture.

    Le rattachement est refait ici : une facture créée ET soumise d'un trait ne
    passe pas par `on_update`."""
    pi_lier_fiche_caisse(doc)
    nom = frappe.db.get_value("Facture Achat a Saisir", {"purchase_invoice": doc.name}, "name")
    if not nom:
        return
    frappe.db.set_value("Facture Achat a Saisir", nom, "statut", "Saisie",
                        update_modified=False)
    _remplacer_avance_par_paiement(doc, nom)


def pi_rouvrir_fiche(doc, method=None):
    """Purchase Invoice on_cancel : la fiche repart dans la file « À saisir ».

    ⚠️ LE PAIEMENT CRÉÉ À LA SOUMISSION EST ANNULÉ AVEC ELLE. Il ne référence
    qu'elle : le laisser vivant laisserait un règlement rattaché à une facture
    annulée, et l'argent sorti sans contrepartie. La fiche garde sa trace (le
    paiement annulé reste consultable) et repart dans la file."""
    nom = frappe.db.get_value("Facture Achat a Saisir", {"purchase_invoice": doc.name}, "name")
    if not nom:
        return
    pe_nom = frappe.db.get_value("Facture Achat a Saisir", nom, "payment_entry")
    if pe_nom and frappe.db.exists("Payment Entry", pe_nom):
        pe = frappe.get_doc("Payment Entry", pe_nom)
        if pe.docstatus == 1:
            pe.flags.ignore_permissions = True
            pe.cancel()
    frappe.db.set_value("Facture Achat a Saisir", nom,
                        {"statut": "À saisir", "purchase_invoice": ""},
                        update_modified=False)
