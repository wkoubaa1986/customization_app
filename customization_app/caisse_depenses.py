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
        _attacher_scan(photo_facture, "facture-%s" % fiche.name,
                       "Facture Achat a Saisir", fiche.name)
        if je:
            _attacher_scan(photo_facture, "facture-%s" % fiche.name,
                           "Journal Entry", je.name)
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


def _redresser_document(image_bytes):
    """Le VRAI scan : OpenCV détecte le quadrilatère de la feuille (Canny +
    contours) et REDRESSE la perspective (warpPerspective) — rendu CamScanner,
    même sur une photo prise de biais.

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
    h, w = img.shape[:2]
    echelle = min(1.0, 700.0 / max(h, w))
    petit = cv2.resize(img, None, fx=echelle, fy=echelle) if echelle < 1 else img
    gris = cv2.cvtColor(petit, cv2.COLOR_BGR2GRAY)
    bords = cv2.Canny(cv2.GaussianBlur(gris, (5, 5), 0), 50, 150)
    bords = cv2.dilate(bords, np.ones((3, 3), np.uint8))
    contours, _rien = cv2.findContours(bords, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    quad = None
    aire_min = 0.25 * petit.shape[0] * petit.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        if cv2.contourArea(c) < aire_min:
            break
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4:
            quad = approx.reshape(4, 2).astype("float32") / echelle
            break
    if quad is None:
        return None
    somme = quad.sum(axis=1)
    diff = np.diff(quad, axis=1).ravel()
    tl, br = quad[np.argmin(somme)], quad[np.argmax(somme)]
    tr, bl = quad[np.argmin(diff)], quad[np.argmax(diff)]
    largeur = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    hauteur = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if largeur < 200 or hauteur < 200:
        return None
    src = np.array([tl, tr, br, bl], dtype="float32")
    dst = np.array([[0, 0], [largeur - 1, 0], [largeur - 1, hauteur - 1],
                    [0, hauteur - 1]], dtype="float32")
    redresse = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst),
                                   (largeur, hauteur))
    ok, buf = cv2.imencode(".jpg", redresse, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return buf.tobytes() if ok else None


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


def _scan_pdf(image_bytes):
    """La photo du justificatif devient un PDF façon SCANNER : niveaux de gris,
    contraste étiré, netteté, taille bornée — lisible et léger, sans dépendance
    nouvelle (Pillow est déjà dans Frappe ; le recadrage de perspective exigerait
    OpenCV, écarté pour ne pas reconstruire l'image de prod).
    Rend les octets du PDF, ou None si l'image est illisible (on garde alors la
    photo brute)."""
    import io

    try:
        from PIL import Image, ImageFilter, ImageOps
        # D'abord le redressement OpenCV (perspective corrigée) ; à défaut, le
        # rognage Pillow (bords coupés, pas de redressement).
        redresse = _redresser_document(image_bytes)
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


def _attacher_scan(photo, nom_base, doctype, name):
    """Attache le justificatif en PDF scanné ; repli sur la photo brute si la
    conversion échoue."""
    from frappe.utils.file_manager import save_file

    contenu, _mt = _decoder(photo)
    pdf = _scan_pdf(contenu)
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
