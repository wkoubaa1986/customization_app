"""
Retour d'un colis Aramex (statut « Returned ») — bouton « 📦 Retour reçu » de
l'écran Livraisons Aramex.

LE GESTE (décisions utilisateur 19/08) :
  1. la PHOTO du colis physiquement revenu est OBLIGATOIRE — le statut Aramex
     annonce un retour, la photo prouve qu'il est arrivé ;
  2. le stock RENTRE par un BL de retour (is_return, adossé au BL d'origine) —
     la voie native : dépôt d'origine, traçabilité article par article ;
  3. le paiement « Dette non payée » sur Livraison Aramex - A&S est SUPPRIMÉ
     (convention maison : annulé puis supprimé, jamais laissé en docstatus 2) —
     Aramex ne versera jamais ce colis, le compte ne doit plus l'attendre ;
  4. la commande est FERMÉE (Closed) et marquée « Retour colis »
     (custom_retour_colis) : pastille en vue liste + bandeau sur la fiche ;
  5. tout se fige sur la fiche Suivi Aramex + un commentaire sur la commande —
     la suppression du paiement ne fait perdre aucune trace.

BLOQUANT : une facture VALIDÉE liée à la commande arrête tout — l'avoir se
traite à la main (décision utilisateur : pas d'avoir automatique).

Retour TOTAL seulement pour l'instant ; le retour partiel (cas Baccari : un
article défectueux, Aramex verse le reste) reste un geste manuel.

LA PHOTO EST VÉRIFIÉE (demande du 19/11/2026)
---------------------------------------------
Le 18/11/2026, le retour de Nabil Inoubli (WEB1-008706, bordereau 55087538380) a été constaté avec la photo d'un
AUTRE colis : la pochette de documents de Warghui Ridha, arrivée ce jour-là. Le vrai colis n'a été livré chez nous
que le lendemain. La photo prouvait qu'un colis était là — pas que c'était le bon.

Deux contrôles, avant tout mouvement de stock :
  1. L'IA LIT L'ÉTIQUETTE de la photo : numéro du bordereau, « Shpr Ref » (le bordereau d'origine, imprimé sur
     l'étiquette de retour), expéditeur. Un colis qui revient porte un NOUVEAU bordereau : seul « Shpr Ref » ou le
     numéro de retour annoncé par Aramex le relie à notre envoi.
  2. L'API ARAMEX FAIT LA CORRESPONDANCE : le suivi de notre bordereau donne le numéro de retour (« Rtn HAWB … »),
     et le suivi de ce numéro dit s'il nous a déjà été LIVRÉ. Un retour encore en route ne peut pas être sur la photo.
Verdicts : « ok » (c'est le bon colis) · « autre » (c'est le colis de quelqu'un d'autre : on dit lequel) ·
« illisible » (reprendre la photo) · « indisponible » (l'IA ne répond pas : l'API seule décide).
Le serveur refuse tout ce qui n'est pas « ok » ; seul un responsable peut passer outre, avec une note, et c'est tracé.
`numeros_de_retour`, `numeros_lus`, `verdict_photo`, `decision` sont PURES (testées sans site).
"""

import base64
import hashlib
import io
import json
import re

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from customization_app.aramex_stock import ENTREPOT_ARAMEX
from customization_app.livraison_aramex import (
    COMPTE_ARAMEX,
    DOCTYPE_SUIVI,
    _sans_accent,
    reference_aramex,
)

ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")

# Le colis retourné revient PHYSIQUEMENT au magasin — quel que soit l'entrepôt
# de départ (souvent désactivé depuis : « Hall - A&S » a bloqué le premier
# essai). Le BL de retour entre donc toujours ici (même règle que la sortie :
# aramex_stock).
ENTREPOT_RETOUR = ENTREPOT_ARAMEX

# Les formes, dans le statut ou la dernière description, qui disent qu'un colis
# REVIENT — sous-ensemble « retour » des mots d'alerte du suivi.
_MOTS_RETOUR = ("return", "retour", "renvoy", "expediteur", "refus", "refused")


def _est_retour(reference):
    """Le suivi connu de ce bordereau annonce-t-il un retour ?"""
    doc = frappe.db.get_value(DOCTYPE_SUIVI, reference,
                              ["statut", "derniere_description"], as_dict=True)
    if not doc:
        return False
    texte = _sans_accent("%s %s" % (doc.statut or "", doc.derniere_description or ""))
    return any(mot in texte for mot in _MOTS_RETOUR)


# ── Vérification de la photo ───────────────────────────────────────────────────────────────────────────────

#: Qui peut constater un retour malgré une vérification défavorable (photo d'un colis sans étiquette, IA en panne…).
ROLES_FORCER = ("System Manager", "Accounts Manager", "Sales Manager")
VERIFICATION_TTL = 1800          # 30 min entre la photo et la confirmation
RE_RETOUR = re.compile(r"Rtn\s*HAWB\s*[:#]?\s*(\d{8,20})", re.I)
RE_TELEPHONE_TN = re.compile(r"216\d{8}")


def _chiffres(valeur) -> str:
    return re.sub(r"\D", "", str(valeur or ""))


def numeros_de_retour(suivi, derniere_description="") -> set:
    """Les numéros de bordereau de RETOUR annoncés par Aramex pour notre envoi (« Rtn HAWB 55087946126 »), lus dans
    tous les événements du suivi et dans la dernière description. PURE."""
    textes = [derniere_description or ""]
    for e in ((suivi or {}).get("evenements") or []):
        textes += [e.get("commentaire") or "", e.get("description") or ""]
    return {n for t in textes for n in RE_RETOUR.findall(t)}


def numeros_lus(lu) -> set:
    """Les numéros de bordereau lus sur la photo. Un téléphone tunisien (216 + 8 chiffres) a la longueur d'un
    bordereau : il est écarté, comme celui que l'IA a rangé dans « telephone ». PURE."""
    lu = lu or {}
    bruts = [lu.get("bordereau"), lu.get("reference_expediteur")] + list(lu.get("autres_numeros") or [])
    telephone = _chiffres(lu.get("telephone"))
    out = set()
    for b in bruts:
        n = _chiffres(b)
        if 8 <= len(n) <= 20 and n != telephone and not RE_TELEPHONE_TN.fullmatch(n):
            out.add(n)
    return out


def _proches(a: str, b: str) -> bool:
    """Deux numéros de même longueur qui ne diffèrent que d'UN chiffre : une lecture douteuse, pas un autre colis."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def verdict_photo(lu, reference, retours) -> str:
    """« ok » / « autre » / « illisible ». PURE.

    ok        : l'étiquette porte notre bordereau (étiquette d'origine, ou « Shpr Ref » de l'étiquette de retour),
                ou le numéro de retour qu'Aramex annonce pour lui.
    illisible : aucun numéro lu — ou un numéro à UN chiffre du bon : on fait reprendre la photo plutôt que
                d'accuser l'employé de s'être trompé de colis sur une faute de lecture.
    autre     : des numéros nets, et aucun n'est le nôtre."""
    attendus = {_chiffres(reference)} | {_chiffres(r) for r in (retours or ())}
    attendus.discard("")
    lus = numeros_lus(lu)
    if not lus:
        return "illisible"
    if lus & attendus:
        return "ok"
    if any(_proches(a, b) for a in lus for b in attendus):
        return "illisible"
    return "autre"


def decision(verdict, retour_livre, peut_forcer=False, forcer=False) -> dict:
    """Peut-on constater le retour ? -> {"autorise", "force"}. PURE.

    `retour_livre` : ce que dit l'API Aramex du bordereau de retour — True (livré chez nous), False (encore en
    route), None (inconnu). Quand l'IA ne répond pas, c'est elle seule qui décide : un retour livré passe.
    `forcer` n'est entendu que d'un responsable (`peut_forcer`)."""
    naturel = verdict == "ok" or (verdict == "indisponible" and retour_livre is True)
    if naturel:
        return {"autorise": True, "force": False}
    if forcer and peut_forcer:
        return {"autorise": True, "force": True}
    return {"autorise": False, "force": False}


def _images_pour_lecture(photo: str) -> list:
    """La photo entière, puis ses deux moitiés (haut / bas, ou gauche / droite) agrandies.

    POURQUOI DES MOITIÉS. Le modèle ramène toute image à 768 px sur son petit côté : sur la photo d'un téléphone
    (2600 × 4600), « Shpr Ref : 55087711265 » — le seul lien écrit avec notre bordereau — ne fait plus que
    quelques pixels de haut, et il a été lu « 5507711265 » puis « 55077711265 » aux essais du 19/11/2026. Chaque
    moitié, avec un recouvrement, double la finesse là où se trouve l'étiquette.
    Sur la moindre difficulté, la photo d'origine est rendue seule."""
    try:
        from PIL import Image, ImageOps

        brut = base64.b64decode(photo.split(",", 1)[-1])
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(brut))).convert("RGB")

        def en_donnees(img, cote):
            img = img.copy()
            img.thumbnail((cote, cote))
            tampon = io.BytesIO()
            img.save(tampon, format="JPEG", quality=88)
            return "data:image/jpeg;base64," + base64.b64encode(tampon.getvalue()).decode()

        largeur, hauteur = image.size
        if hauteur >= largeur:      # portrait : haut et bas, 60 % chacun (20 % de recouvrement)
            moities = [image.crop((0, 0, largeur, int(hauteur * 0.6))),
                       image.crop((0, int(hauteur * 0.4), largeur, hauteur))]
        else:
            moities = [image.crop((0, 0, int(largeur * 0.6), hauteur)),
                       image.crop((int(largeur * 0.4), 0, largeur, hauteur))]
        return [en_donnees(image, 1600)] + [en_donnees(m, 2000) for m in moities]
    except Exception:
        return [photo]


def lire_etiquette(photo) -> dict:
    """L'IA lit l'étiquette Aramex de la photo. -> {"lisible", "bordereau", "reference_expediteur", "expediteur",
    "ville", "telephone", "description", "autres_numeros"} ou {"erreur": …}. Ne lève jamais."""
    if not (photo or "").startswith("data:image/"):
        return {"erreur": _("La pièce jointe n'est pas une photo.")}
    try:
        from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

        client_ia, model, _t = _get_client_model_temp()
        res = client_ia.responses.create(
            model=model,
            instructions=(
                "Tu lis la photo d'un colis ou d'une pochette Aramex revenu chez l'expéditeur. Il porte une ou "
                "plusieurs étiquettes Aramex (parfois à l'envers ou froissées). Réponds STRICTEMENT en JSON : "
                '{"lisible": <true si au moins une étiquette Aramex est lisible>, '
                '"bordereau": "<le numéro imprimé sous le code-barres principal de l\'étiquette (waybill / HAWB), '
                'souvent 11 chiffres, EN CHAÎNE ; null si illisible>", '
                '"reference_expediteur": "<le numéro imprimé après « Shpr Ref » / « Shipper Ref », EN CHAÎNE ; null si absent>", '
                '"expediteur": "<le nom après « From » / « Account » ; null si illisible>", '
                '"ville": "<la ville de l\'expéditeur ; null si illisible>", '
                '"telephone": "<le téléphone de l\'expéditeur (après « Tel » dans le bloc From) ; null si absent>", '
                '"description": "<le texte après « Description » et « Remarks » ; null si absent>", '
                '"autres_numeros": ["<tout AUTRE numéro de bordereau visible (8 à 20 chiffres) sur une autre '
                'étiquette du colis — jamais un téléphone, un code postal ou une date>"]}. '
                "Recopie chaque numéro INTÉGRALEMENT, chiffre par chiffre, sans en inventer : en cas de doute "
                "sur un chiffre, mets null. Les numéros ne sont JAMAIS des nombres JSON."),
            input=[{"role": "user", "content": [
                {"type": "input_image", "image_url": image, "detail": "high"}
                for image in _images_pour_lecture(photo)] + [
                {"type": "input_text", "text": "C'est UNE SEULE photo : d'abord entière, puis ses deux moitiés "
                 "agrandies (elles se recouvrent). Lis l'étiquette Aramex de ce colis revenu ; sers-toi des "
                 "agrandissements pour les petits caractères (« Shpr Ref »)."}]}])
        texte = (res.output_text or "").strip().strip("`")
        if texte.lower().startswith("json"):
            texte = texte.split("\n", 1)[1]
        lu = json.loads(texte)
    except Exception:
        frappe.log_error(title="Retour Aramex : lecture de l'étiquette indisponible", message=frappe.get_traceback())
        return {"erreur": _("Lecture automatique de l'étiquette indisponible.")}
    if not isinstance(lu, dict):
        return {"erreur": _("Lecture automatique de l'étiquette indisponible.")}
    return {"lisible": bool(lu.get("lisible")), "bordereau": _chiffres(lu.get("bordereau")),
            "reference_expediteur": _chiffres(lu.get("reference_expediteur")),
            "expediteur": str(lu.get("expediteur") or "")[:80], "ville": str(lu.get("ville") or "")[:60],
            "telephone": _chiffres(lu.get("telephone")), "description": str(lu.get("description") or "")[:120],
            "autres_numeros": [_chiffres(n) for n in (lu.get("autres_numeros") or []) if _chiffres(n)][:6]}


def _suivi_frais(reference, timeout=25):
    """Le suivi du bordereau, relu à l'instant chez Aramex ; à défaut, le dernier connu. Lecture seule."""
    from customization_app.livraison_aramex import _lire_suivi, interroger

    try:
        suivi = interroger(reference, timeout=timeout)
        if suivi and not suivi.get("erreur"):
            return suivi
    except Exception:
        pass
    return _lire_suivi(reference) or {}


def distance(a: str, b: str) -> int:
    """Nombre de chiffres à ajouter, retirer ou changer pour passer de `a` à `b` (Levenshtein). PURE."""
    if a == b:
        return 0
    precedent = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        courant = [i]
        for j, cb in enumerate(b, start=1):
            courant.append(min(precedent[j] + 1, courant[j - 1] + 1, precedent[j - 1] + (ca != cb)))
        precedent = courant
    return precedent[-1]


def candidats_proches(numero_lu: str, references, tolerance=2) -> list:
    """Nos bordereaux qui ressemblent au numéro lu (2 chiffres d'écart au plus), du plus proche au moins proche.
    Une lecture de petits caractères perd ou change volontiers un chiffre : « 5507711265 » pour « 55087711265 ».
    Ce n'est qu'une PISTE — c'est l'API Aramex qui confirme (`_colis_connu`). PURE."""
    if not numero_lu:
        return []
    notes = sorted((distance(numero_lu, r), r) for r in references if abs(len(r) - len(numero_lu)) <= tolerance)
    return [r for d, r in notes if d <= tolerance]


JOURS_RECENTS = 60          # un retour revient en quelques jours : on ne cherche pas dans toute l'histoire


def _fiche_colis(reference, sur=True) -> dict:
    doc = frappe.db.get_value(DOCTYPE_SUIVI, reference, ["name as reference", "commande", "destinataire", "ville",
                                                         "statut", "retour_recu_le", "livre"], as_dict=True)
    if not doc:
        return {}
    colis = dict(doc, sur=sur)
    colis["retour_recu_le"] = str(colis.get("retour_recu_le") or "")[:16]
    if colis.get("commande"):
        colis["client"] = frappe.db.get_value("Sales Order", colis["commande"], "customer_name") or ""
    return colis


def _colis_connu(lu) -> dict:
    """À quel envoi appartient l'étiquette lue ? -> {reference, commande, destinataire, ville, statut, sur} ou {}.

    1. un numéro lu EST l'un de nos bordereaux (étiquette d'origine, ou « Shpr Ref » bien lu) ;
    2. le bordereau lu est un retour déjà annoncé dans un suivi que nous avons en base ;
    3. sinon « Shpr Ref », lu à un ou deux chiffres près, désigne des candidats — et L'API ARAMEX TRANCHE : le
       bon est celui dont le suivi annonce justement ce bordereau de retour. Sans confirmation, un candidat unique
       est rendu comme « probable » (sur = False) : l'écran le dit, il n'affirme pas."""
    numeros = sorted(numeros_lus(lu))
    if not numeros:
        return {}
    exact = frappe.get_all(DOCTYPE_SUIVI, filters={"name": ["in", numeros]}, pluck="name", limit=1)
    if exact:
        return _fiche_colis(exact[0])
    for n in numeros:
        annonce = frappe.get_all(DOCTYPE_SUIVI, pluck="name", limit=1,
                                 or_filters=[["derniere_description", "like", "%Rtn HAWB " + n + "%"],
                                             ["payload", "like", "%Rtn HAWB " + n + "%"]])
        if annonce:
            return _fiche_colis(annonce[0])
    recents = frappe.get_all(DOCTYPE_SUIVI, pluck="name", limit=0,
                             filters={"modified": [">=", frappe.utils.add_days(frappe.utils.nowdate(), -JOURS_RECENTS)]})
    candidats = candidats_proches(_chiffres((lu or {}).get("reference_expediteur")), recents)[:3]
    bordereau = _chiffres((lu or {}).get("bordereau"))
    for candidat in candidats:
        if bordereau and bordereau in numeros_de_retour(_suivi_frais(candidat, timeout=20)):
            return _fiche_colis(candidat)
    return _fiche_colis(candidats[0], sur=False) if len(candidats) == 1 else {}


def verifier(reference, photo) -> dict:
    """Le contrôle complet d'une photo pour UN bordereau attendu. Lecture seule, ne lève jamais.

    -> {verdict, lu, attendu: {reference, retours, retour_livre, retour_statut}, autre: {…} | None}"""
    doc = frappe.db.get_value(DOCTYPE_SUIVI, reference, ["derniere_description", "destinataire", "commande"],
                              as_dict=True) or {}
    # La correspondance par l'API : le numéro de retour de NOTRE bordereau, puis l'état de ce retour.
    suivi = _suivi_frais(reference)
    retours = numeros_de_retour(suivi, doc.get("derniere_description"))
    retour_livre, retour_statut = None, ""
    for numero in sorted(retours):
        etat = _suivi_frais(numero, timeout=20)
        if etat:
            retour_livre = bool(etat.get("livre")) or bool(retour_livre)
            retour_statut = etat.get("statut") or retour_statut
    attendu = {"reference": reference, "retours": sorted(retours), "retour_livre": retour_livre,
               "retour_statut": retour_statut, "destinataire": doc.get("destinataire") or ""}

    lu = lire_etiquette(photo)
    if lu.get("erreur"):
        return {"verdict": "indisponible", "lu": {}, "attendu": attendu, "autre": None, "erreur": lu["erreur"]}
    verdict = verdict_photo(lu, reference, retours)
    autre = None
    if verdict == "autre":
        autre = _colis_connu(lu)
        # Ce que dit Aramex du bordereau photographié : « livré chez nous le … » aide à comprendre l'erreur.
        if lu.get("bordereau"):
            etat = _suivi_frais(lu["bordereau"], timeout=20)
            autre = dict(autre or {}, etiquette_statut=etat.get("statut") or "",
                         etiquette_date=((etat.get("evenements") or [{}])[0].get("date") or ""))
    return {"verdict": verdict, "lu": lu, "attendu": attendu, "autre": autre}


def _reference_du_paiement(payment_entry):
    pe = frappe.get_doc("Payment Entry", payment_entry)
    return pe, reference_aramex(pe.reference_no)


def _cle_verification(payment_entry) -> str:
    return "retour_aramex_verif:%s:%s" % (frappe.session.user, payment_entry)


@frappe.whitelist()
def verifier_photo(payment_entry, photo):
    """Dès que la photo est prise : est-ce bien LE colis de ce bordereau ? Rien n'est écrit. Le résultat est gardé
    30 minutes pour que « Constater le retour » ne refasse pas la lecture — mais c'est le serveur qui le garde :
    l'écran ne peut pas s'en inventer un."""
    frappe.only_for(ROLES)
    _pe, reference = _reference_du_paiement(payment_entry)
    if not reference:
        frappe.throw(_("Ce paiement ne porte pas de bordereau Aramex."))
    resultat = verifier(reference, photo)
    resultat["empreinte"] = hashlib.sha1((photo or "").encode()).hexdigest()
    resultat["peut_forcer"] = bool(set(frappe.get_roles()) & set(ROLES_FORCER))
    frappe.cache().set_value(_cle_verification(payment_entry), resultat, expires_in_sec=VERIFICATION_TTL)
    return resultat


def _resume_verification(v, force) -> str:
    lu = v.get("lu") or {}
    lecture = ", ".join(filter(None, [
        ("bordereau %s" % lu["bordereau"]) if lu.get("bordereau") else "",
        ("réf. expéditeur %s" % lu["reference_expediteur"]) if lu.get("reference_expediteur") else "",
        lu.get("expediteur") or ""])) or "rien de lisible"
    etats = {"ok": "✅ correspond au bordereau", "autre": "❌ NE correspond PAS au bordereau",
             "illisible": "⚠️ étiquette illisible", "indisponible": "⚠️ lecture automatique indisponible"}
    texte = "Vérification de la photo : %s (étiquette lue : %s)." % (etats.get(v.get("verdict"), "?"), lecture)
    if (v.get("attendu") or {}).get("retour_livre") is not None:
        texte += " Retour %s par Aramex." % ("livré chez nous" if v["attendu"]["retour_livre"] else "PAS ENCORE livré")
    if force:
        texte += " ⚠️ Constaté QUAND MÊME par %s." % frappe.session.user
    return texte


def _pieces_du_paiement(pe_name):
    """La commande visée par le paiement Aramex, ses BL validés et ses factures."""
    refs = frappe.get_all("Payment Entry Reference",
                          filters={"parent": pe_name},
                          fields=["reference_doctype", "reference_name"])
    so = next((r.reference_name for r in refs
               if r.reference_doctype == "Sales Order"), None)
    sis = [r.reference_name for r in refs if r.reference_doctype == "Sales Invoice"]
    if not so and sis:
        so = frappe.db.get_value("Sales Invoice Item",
                                 {"parent": sis[0], "sales_order": ("!=", "")},
                                 "sales_order")
    if not so:
        frappe.throw(_("Le paiement {0} ne référence aucune commande.").format(pe_name))
    dns = [r.parent for r in frappe.get_all(
        "Delivery Note Item", filters={"against_sales_order": so},
        fields=["parent"], distinct=True)]
    dns_valides = [d for d in frappe.get_all(
        "Delivery Note", filters={"name": ("in", dns), "docstatus": 1},
        pluck="name")] if dns else []
    sis_so = frappe.db.sql(
        """SELECT DISTINCT si.name FROM `tabSales Invoice` si
           INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
           WHERE sii.sales_order = %s AND si.docstatus = 1""", (so,))
    factures_validees = sorted({r[0] for r in sis_so} | {
        s for s in sis
        if frappe.db.get_value("Sales Invoice", s, "docstatus") == 1})
    return so, dns_valides, factures_validees


def _valider_bl_retour(doc):
    """Soumet le BL de retour comme le contrôle de clôture soumet un BL : état
    de workflow « Approved » puis soumission sous in_import."""
    if doc.get("workflow_state") is not None:
        doc.db_set("workflow_state", "Approved", update_modified=False)
        doc.reload()
    frappe.flags.in_import = True
    try:
        doc.submit()
    finally:
        frappe.flags.in_import = False


@frappe.whitelist()
def constater_retour(payment_entry, photo, photo_nom=None, note=None, forcer=0):
    """Constate le retour physique d'un colis Aramex : photo VÉRIFIÉE, BL(s) de retour,
    suppression du paiement dette, commande fermée et marquée."""
    frappe.only_for(ROLES)

    pe = frappe.get_doc("Payment Entry", payment_entry)
    if pe.docstatus != 1:
        frappe.throw(_("Le paiement {0} n'est pas validé.").format(payment_entry))
    if pe.paid_to != COMPTE_ARAMEX:
        frappe.throw(_("Le paiement {0} n'est pas sur le compte {1}.")
                     .format(payment_entry, COMPTE_ARAMEX))

    reference = reference_aramex(pe.reference_no)
    if not reference or not _est_retour(reference):
        frappe.throw(_("Le suivi Aramex de ce colis n'annonce pas un retour — "
                       "rafraîchissez le suivi avant de constater le retour."))
    if frappe.db.get_value(DOCTYPE_SUIVI, reference, "retour_recu_le"):
        frappe.throw(_("Le retour du bordereau {0} est déjà constaté.").format(reference))

    if not (photo or "").strip():
        frappe.throw(_("La photo du colis retourné est obligatoire."))

    # ── 0. La photo est-elle celle de CE colis ? Avant tout mouvement de stock. ─────────────────────────────
    # Le résultat gardé par `verifier_photo` n'est repris que s'il porte sur LA MÊME photo ; sinon on relit ici :
    # l'écran ne décide jamais seul.
    empreinte = hashlib.sha1(photo.encode()).hexdigest()
    verification = frappe.cache().get_value(_cle_verification(payment_entry)) or {}
    if verification.get("empreinte") != empreinte:
        verification = verifier(reference, photo)
    peut_forcer = bool(set(frappe.get_roles()) & set(ROLES_FORCER))
    choix = decision(verification.get("verdict"), (verification.get("attendu") or {}).get("retour_livre"),
                     peut_forcer=peut_forcer, forcer=frappe.utils.cint(forcer))
    if not choix["autorise"]:
        autre = verification.get("autre") or {}
        frappe.throw({
            "autre": _("Cette photo n'est pas celle du colis {0} : l'étiquette est celle de {1}. "
                       "Rien n'a été enregistré.").format(
                reference, autre.get("destinataire") or autre.get("client")
                or (verification.get("lu") or {}).get("expediteur") or _("un autre envoi")),
            "illisible": _("L'étiquette Aramex n'est pas lisible sur la photo : reprenez-la, étiquette bien "
                           "visible. Rien n'a été enregistré."),
        }.get(verification.get("verdict"),
              _("La photo n'a pas pu être vérifiée et Aramex n'annonce pas ce retour comme livré chez nous. "
                "Rien n'a été enregistré.")))
    if choix["force"] and not (note or "").strip():
        frappe.throw(_("Écrivez dans la note pourquoi vous constatez ce retour malgré la vérification."))
    frappe.cache().delete_value(_cle_verification(payment_entry))

    so, dns, factures = _pieces_du_paiement(pe.name)
    if factures:
        # Décision utilisateur : pas d'avoir automatique — on s'arrête net.
        frappe.throw(_("Facture validée liée ({0}) : à traiter manuellement "
                       "(avoir) avant de constater le retour.")
                     .format(", ".join(factures)))
    if not dns:
        frappe.throw(_("Aucun bon de livraison validé sur {0} — le stock n'est "
                       "jamais sorti, rien à ré-entrer. Traitez le paiement à la main.")
                     .format(so))
    deja = frappe.get_all("Delivery Note",
                          filters={"is_return": 1, "return_against": ("in", dns),
                                   "docstatus": ("<", 2)}, pluck="name")
    if deja:
        frappe.throw(_("Un BL de retour existe déjà : {0}.").format(", ".join(deja)))

    # ── 1. Le stock rentre : un BL de retour TOTAL par BL d'origine ──────────
    from erpnext.controllers.sales_and_purchase_return import make_return_doc
    bls_retour = []
    for dn in dns:
        rd = make_return_doc("Delivery Note", dn)
        rd.set_warehouse = ENTREPOT_RETOUR
        for item in rd.items:
            item.warehouse = ENTREPOT_RETOUR
        rd.insert(ignore_permissions=True)
        _valider_bl_retour(rd)
        bls_retour.append(rd.name)

    # ── 2. La photo, preuve du retour : sur le Suivi, le 1er BL de retour ET
    #       la commande (demande utilisateur 19/08 — la preuve doit se voir
    #       depuis la pièce que tout le monde ouvre en premier) ─────────────
    from frappe.utils.file_manager import save_file
    contenu = base64.b64decode(photo.split(",", 1)[-1])
    nom_fichier = photo_nom or "retour-%s.jpg" % reference
    fichier = save_file(nom_fichier, contenu, DOCTYPE_SUIVI, reference, is_private=1)
    save_file(nom_fichier, contenu, "Delivery Note", bls_retour[0], is_private=1)
    save_file(nom_fichier, contenu, "Sales Order", so, is_private=1)

    # ── 3. Le paiement dette disparaît (convention maison), trace d'abord ────
    trace = ("📦 Retour de colis Aramex constaté le %s par %s — bordereau %s. "
             "BL de retour : %s. Paiement dette supprimé : %s (%s DT, réf « %s »). "
             "Photo attachée au Suivi Aramex. %s%s") % (
        str(now_datetime())[:16], frappe.session.user, reference,
        ", ".join(bls_retour), pe.name, flt(pe.paid_amount, 3), pe.reference_no or "",
        _resume_verification(verification, choix["force"]),
        (" Note : %s" % note.strip()) if (note or "").strip() else "")
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Sales Order", "reference_name": so,
        "content": trace,
    }).insert(ignore_permissions=True)

    montant = flt(pe.paid_amount, 3)
    pe.flags.ignore_permissions = True
    pe.cancel()
    pe.delete(ignore_permissions=True)

    # ── 4. La commande se ferme et porte la marque « Retour colis » ──────────
    so_doc = frappe.get_doc("Sales Order", so)
    if so_doc.docstatus == 1 and so_doc.status != "Closed":
        so_doc.update_status("Closed")
    frappe.db.set_value("Sales Order", so, "custom_retour_colis", 1,
                        update_modified=False)

    # ── 5. Tout se fige sur la fiche Suivi Aramex ────────────────────────────
    frappe.db.set_value(DOCTYPE_SUIVI, reference, {
        "retour_recu_le": now_datetime(),
        "retour_par": frappe.session.user,
        "retour_bl": ", ".join(bls_retour),
        "retour_pe": pe.name,
        "retour_montant": montant,
        "retour_note": (note or "").strip(),
    }, update_modified=False)

    frappe.db.commit()
    return {"commande": so, "bls_retour": bls_retour, "pe_supprimee": pe.name,
            "montant": montant, "photo": fichier.file_url}
