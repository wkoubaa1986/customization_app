"""Revue des dépenses à la collecte (demande du 24/11/2026, suite de CITERNES PLAST n° 2174 payée deux fois).

Sur la carte de collecte, chaque dépense de la caisse du jour apparaît avec sa photo, son fournisseur, son montant et
des signaux : doublon possible (même montant, même fournisseur — ou même objet — dans n'importe quelle caisse, à
7 jours près), saisie par un autre que le payeur, accord préalable utilisé (« accordée par … ») ou espèces au-delà du
seuil SANS accord, montant élevé, sans n° de facture, sans photo. Le collecteur marque chacune « vue » ou la conteste avec un commentaire ; la collecte
est refusée tant qu'une dépense n'est pas passée en revue.

Une contestation NE BLOQUE PAS la collecte (l'argent est compté) : elle s'écrit sur la dépense et prévient son
auteur et les responsables, et le PDF de clôture la montre. Le collecteur qui collecte SA caisse n'a rien à revoir.
"""
import json
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate

SEUIL_DEFAUT = 200.0
FENETRE_DOUBLON = 7
COMMENTAIRE_MIN = 10
VU, CONTESTE = "vu", "conteste"


def seuil_accord() -> float:
    """Config Caisse › « Accord préalable au-delà de » : 200 DT tant que personne n'a réglé le champ ; 0 = désactivé."""
    ligne = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = 'seuil_accord_depense'")
    return SEUIL_DEFAUT if not ligne or ligne[0][0] in (None, "") else flt(ligne[0][0])


def cle_tiers(texte) -> str:
    """Fournisseur (ou objet) réduit à ses lettres et chiffres, sans accents ni casse. Fonction pure."""
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", texte)


def signaux_depense(ligne, voisines, seuil) -> list:
    """Les signaux d'une dépense : [{code, niveau (rouge | orange | gris), texte}]. `voisines` : les dépenses de toutes
    les caisses autour de la date (même forme, avec `cle`). Fonction pure."""
    s = []
    for v in voisines:
        if (v["name"] != ligne["name"] and ligne.get("cle") and v.get("cle") == ligne["cle"]
                and abs(flt(v.get("montant")) - flt(ligne.get("montant"))) < 0.0005):
            s.append({"code": "doublon", "niveau": "rouge", "texte": "Doublon possible : %s, même montant, %s par %s le %s" % (
                "même fournisseur" if ligne.get("fournisseur") else "même objet", v["name"], v.get("saisi_par") or "?",
                getdate(v["date"]).strftime("%d/%m/%Y"))})
            break
    if ligne.get("payee_par_collegue"):
        s.append({"code": "collegue", "niveau": "orange", "texte": "Saisie par %s, payée de la caisse de %s" % (
            ligne.get("saisi_par") or "?", ligne.get("caisse") or "?")})
    # L'accord préalable (25/11/2026, « rendre le circuit lisible ») : la dépense qui l'a utilisé le montre ; une part
    # en ESPÈCES au-delà du seuil SANS accord (correction, « Dépenses à payer », « Factures à payer », paiement ERPNext…)
    # est signalée — c'est ce que l'accord devait empêcher.
    accord = ligne.get("accord")
    if accord:
        s.append({"code": "accord", "niveau": "vert", "texte": "Accordée par %s (%s, %s DT)" % (
            accord.get("par") or "?", accord.get("name"), _fmt(accord.get("montant")))})
    elif seuil and flt(ligne.get("especes")) > seuil + 0.0005 and not ligne.get("par_approbateur"):
        s.append({"code": "sans_accord", "niveau": "rouge", "texte": "%s DT en espèces, au-delà de %s DT, SANS accord" % (
            _fmt(ligne.get("especes")), _fmt(seuil))})
    elif seuil and flt(ligne.get("montant")) >= seuil:
        s.append({"code": "eleve", "niveau": "orange", "texte": "Montant élevé (%s DT ou plus)" % _fmt(seuil)})
    if not ligne.get("numero_facture"):
        facturee = "facture" in (ligne.get("type") or "").lower() and "non" not in (ligne.get("type") or "").lower()
        s.append({"code": "sans_facture", "niveau": "orange" if facturee else "gris",
                  "texte": "Sans n° de facture" if facturee else "Dépense sans facture"})
    if not ligne.get("photos"):
        s.append({"code": "sans_photo", "niveau": "orange", "texte": "Sans photo"})
    return s


def _fmt(v) -> str:
    return "{:,.3f}".format(flt(v)).replace(",", " ").replace(".", ",")


def _noms_par_user() -> dict:
    noms = {e.user_id: e.employee_name for e in frappe.get_all(
        "Employee", filters={"user_id": ["is", "set"]}, fields=["user_id", "employee_name"])}
    for u in frappe.get_all("User", filters={"user_type": "System User"}, fields=["name", "full_name"]):
        noms.setdefault(u.name, u.full_name)
    return noms


def _completer(lignes):
    """Fournisseur, n° de facture et clé de doublon de chaque ligne (lectures groupées)."""
    from customization_app import caisse_depenses as D
    jv = sorted({l["name"] for l in lignes if l["doctype"] == "Journal Entry"})
    pe = sorted({l["name"] for l in lignes if l["doctype"] == "Payment Entry"})
    infos = {}
    if jv:
        for r in frappe.db.sql("SELECT name, owner, cheque_no, user_remark FROM `tabJournal Entry` WHERE name IN %(n)s",
                               {"n": jv}, as_dict=True):
            lu = D.lire_saisie(r.cheque_no, r.user_remark, [])
            infos[r.name] = {"fournisseur": lu.get("fournisseur") or "", "numero_facture": lu.get("numero_facture") or "",
                             "owner": r.owner}
    if pe:
        for r in frappe.db.sql("""SELECT pe.name, pe.owner, pe.party_name, pe.reference_no,
                                         MAX(f.supplier) AS supplier, MAX(f.numero_facture) AS numero_facture
                                  FROM `tabPayment Entry` pe
                                  LEFT JOIN `tabFacture Achat a Saisir` f ON f.payment_entry = pe.name
                                  WHERE pe.name IN %(n)s GROUP BY pe.name""", {"n": pe}, as_dict=True):
            infos[r.name] = {"fournisseur": r.supplier or r.party_name or "", "numero_facture": r.numero_facture or "",
                             "owner": r.owner}
    for l in lignes:
        l.update(infos.get(l["name"]) or {})
        l["cle"] = cle_tiers(l.get("fournisseur") or (l.get("description") or "").split(" — ")[0])


def depenses_a_revoir(caisse, date) -> list:
    """Les dépenses de la caisse ce jour-là, détaillées et signalées. Vide pour la caisse globale."""
    from customization_app.caisse_cloture import CAISSE_GLOBALE
    from customization_app.caisse_depenses import _pieces_jointes
    from customization_app.rapport_caisse_journaliere import _depenses_caisse
    if not caisse or caisse == CAISSE_GLOBALE:
        return []
    date = getdate(date)
    fin = min(add_days(date, FENETRE_DOUBLON), getdate(nowdate()))
    toutes = _depenses_caisse(str(add_days(date, -FENETRE_DOUBLON)), str(max(fin, date)), _noms_par_user())
    du_jour = [l for l in toutes if l["date"] == str(date) and (l.get("caisse") or l["saisi_par"]) == caisse]
    if not du_jour:
        return []
    _completer(toutes)
    seuil = seuil_accord()
    from customization_app.caisse_accord import DOCTYPE as ACCORD, UTILISE, approbateurs
    accords = {a.journal_entry: {"name": a.name, "par": frappe.utils.get_fullname(a.decide_par), "montant": a.montant}
               for a in frappe.get_all(ACCORD, filters={"statut": UTILISE,
                                                        "journal_entry": ["in", list({l["name"] for l in du_jour})]},
                                       fields=["name", "journal_entry", "decide_par", "montant"])}
    responsables = set(approbateurs()) | {"Administrator"}
    vus = set()
    out = []
    for l in du_jour:
        if l["name"] in vus:          # dépense fractionnée : une ligne par part, une seule revue
            continue
        vus.add(l["name"])
        parts = [x for x in du_jour if x["name"] == l["name"]]
        l["montant"] = flt(sum(flt(x["montant"]) for x in parts), 3)
        l["especes"] = flt(sum(flt(x["montant"]) for x in parts if x.get("mode") == "Espèces"), 3)
        l["accord"] = accords.get(l["name"])
        l["par_approbateur"] = l.get("owner") in responsables
        photos = _pieces_jointes(l["doctype"], l["name"])
        l["photos"] = [{"url": f.file_url, "nom": f.file_name} for f in photos]
        l["signaux"] = signaux_depense(l, toutes, seuil)
        out.append(l)
    return out


def lire_revue(texte) -> dict:
    try:
        r = json.loads(texte or "{}")
        return r if isinstance(r, dict) else {}
    except Exception:
        return {}


def verifier_revue(depenses, revue) -> list:
    """Les erreurs qui empêchent la collecte : dépense pas passée en revue, contestation sans commentaire. Pure."""
    erreurs = []
    manquantes = [l for l in depenses if (revue.get(l["name"]) or {}).get("verdict") not in (VU, CONTESTE)]
    if manquantes:
        erreurs.append(_("Passez en revue chaque dépense de la caisse ({0} restante(s) : {1}).").format(
            len(manquantes), ", ".join(l["name"] for l in manquantes)))
    for l in depenses:
        r = revue.get(l["name"]) or {}
        if r.get("verdict") == CONTESTE and len(" ".join((r.get("commentaire") or "").split())) < COMMENTAIRE_MIN:
            erreurs.append(_("Dépense {0} contestée : expliquez pourquoi ({1} caractères minimum).").format(
                l["name"], COMMENTAIRE_MIN))
    return erreurs


def appliquer_revue(cl, revue_envoyee, moi=None):
    """Dans `collecter` : contrôle la revue et l'écrit sur la clôture (cl.revue_depenses, sans sauvegarder). Les
    contestations NOUVELLES s'écrivent sur la dépense et préviennent son auteur et les responsables."""
    moi = moi or frappe.session.user
    depenses = depenses_a_revoir(cl.caisse, cl.date_cloture)
    if not depenses:
        return []
    deja = lire_revue(cl.get("revue_depenses"))
    envoyee = revue_envoyee if isinstance(revue_envoyee, dict) else lire_revue(revue_envoyee)
    revue = dict(deja)
    for nom, r in (envoyee or {}).items():
        if isinstance(r, dict) and r.get("verdict") in (VU, CONTESTE):
            revue[nom] = {"verdict": r["verdict"], "commentaire": " ".join((r.get("commentaire") or "").split())}
    erreurs = verifier_revue(depenses, revue)
    if erreurs:
        frappe.throw("<br>".join(erreurs), title=_("Revue des dépenses"))
    maintenant = str(now_datetime())[:16]
    nouvelles = []
    for l in depenses:
        r = revue[l["name"]]
        avant = deja.get(l["name"]) or {}
        if avant.get("verdict") != r["verdict"] or avant.get("commentaire") != r.get("commentaire"):
            r.update({"par": moi, "le": maintenant})
            if r["verdict"] == CONTESTE:
                nouvelles.append((l, r))
        else:
            r.update({"par": avant.get("par") or moi, "le": avant.get("le") or maintenant})
    cl.revue_depenses = json.dumps({l["name"]: revue[l["name"]] for l in depenses}, ensure_ascii=False)
    for l, r in nouvelles:
        _signaler_contestation(cl, l, r, moi)
    return depenses


def _signaler_contestation(cl, ligne, r, moi):
    from customization_app import caisse_collecte as CC
    texte = "✋ Dépense contestée à la collecte de la caisse %s du %s par %s : %s" % (
        cl.caisse, getdate(cl.date_cloture).strftime("%d/%m/%Y"), frappe.utils.get_fullname(moi), r.get("commentaire"))
    try:
        frappe.get_doc(ligne["doctype"], ligne["name"]).add_comment("Comment", texte)
    except Exception:
        frappe.log_error(title="Revue des dépenses : commentaire sur %s" % ligne["name"])
    sujet = "Dépense %s (%s DT) contestée par %s : %s" % (
        ligne["name"], _fmt(ligne.get("montant")), frappe.utils.get_fullname(moi), r.get("commentaire"))
    for u in {ligne.get("owner")} | set(CC.config()["responsables"]):
        if u and u != moi:
            CC._prevenir(u, sujet, ligne["doctype"], ligne["name"])
