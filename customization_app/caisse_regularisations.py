"""Régulariser les anomalies de « Où est l'argent liquide », et l'inventaire du collecteur (demande du 25/11/2026).

SA DEMANDE : « les erreurs identifiées, ne pas les montrer, les corriger, pour que dans le futur j'aie une meilleure
cartographie ». La page /app/argent-liquide reconstruit qui tient l'argent ; elle affichait, jour après jour, les mêmes
anomalies du passé (un saut de chaîne de 5 293 DT du 20/10, deux écritures « Achat divers » saisies à l'envers, cinq
paiements antidatés). Une anomalie n'est plus une alerte permanente :

  · chaque anomalie a une CLÉ (« saut:CLO-1055 », « jv:ACC-JV-2026-01040 », « antidate:ACC-PAY-… ») et une ou deux
    DÉCISIONS possibles, chacune avec son effet sur la carte (« cet argent n'existe pas : retirer 5 293 DT de la
    caisse », « c'était une dépense saisie à l'envers : retirer deux fois le montant », « vu, rien à corriger ») ;
  · la DIRECTION choisit, écrit le motif : un document « Regularisation Caisse » garde la décision, l'anomalie quitte la
    carte et passe au journal ; l'effet corrige ce que la personne est censée tenir ;
  · l'INVENTAIRE du collecteur principal : il compte tout ce qu'il a en main ; l'écart avec la carte est enregistré
    comme une régularisation, et toutes ses anomalies antérieures sont absorbées — la carte repart du réel ;
  · l'inventaire du COFFRE (« Sabah Espèces ») : compté contre le solde comptable, pour mémoire (l'écart se corrige
    en comptabilité, pas ici).

À LA SAISIE (au lieu d'une alerte après coup) : une écriture MANUELLE qui fait ENTRER de l'argent dans « Espèces » depuis
un compte qui n'est ni le coffre ni une banque est refusée (`controler_ecriture_especes`) — c'est ce qu'étaient les deux
« Achat divers Néjib » : des achats, saisis à l'envers. Les paiements « Espèces » posés sur un compte de banque ne sont
PAS touchés : ce sont des versements en espèces sur le compte, choisis par lui (sa précision du 25/11/2026).

Les fonctions pures n'utilisent pas `frappe.utils.flt` (hors site, il rend 0).
"""
from __future__ import annotations

import frappe
from frappe import _

DOCTYPE = "Regularisation Caisse"
COFFRE = "Sabah Espèces"
INVENTAIRE, INVENTAIRE_COFFRE = "Inventaire", "Inventaire du coffre"
MOTIF_MIN = 10
#: Les enregistrements faits À LA MAIN dans le formulaire (ou par l'API générique) : les outils de caisse passent par
#: leurs propres méthodes et ne sont pas concernés.
SAISIES_MANUELLES = ("frappe.desk.form.save.savedocs", "frappe.client.save", "frappe.client.insert",
                     "frappe.client.submit", "frappe.client.insert_many")
ROLES_DECIDER = ("System Manager",)


def _m(valeur) -> float:
    try:
        return round(float(valeur or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _dt(valeur) -> str:
    entier, _sep, millimes = f"{_m(valeur):,.3f}".replace(",", " ").partition(".")
    return f"{entier},{millimes}"


def _jour(iso) -> str:
    a, m, j = str(iso)[:10].split("-")
    return f"{j}/{m}/{a}"


# ---------------------------------------------------------------------------------------------------------------- pur
def construire_anomalies(sauts, entrees_manuelles, antidates, inattendus, entrees, caisse_principal) -> list:
    """Les anomalies de la carte, chacune avec sa clé, son explication et les décisions possibles. PURE.
    `sauts` : argent_liquide.sauts_de_chaine ; `entrees_manuelles` : écritures qui font ENTRER de l'argent dans Espèces
    sans venir du coffre ni d'une banque ; `antidates` : paiements espèces saisis après le départ mais datés d'avant ;
    `inattendus` : paiements « Espèces » posés sur un compte qui n'est ni une caisse, ni une banque, ni un partenaire ;
    `entrees` : toutes les entrées par écriture (pour expliquer un saut) ; -> [{cle, nature, caisse, poche, date,
    document_type, document, montant, titre, explication, options: [{code, libelle, effet}]}]"""
    out = []
    for x in sauts or []:
        saut = _m(x.get("saut"))
        # L'explication la plus fréquente : un apport du coffre ce jour-là, que la clôture ne comptait pas en entrée.
        le_jour = [e for e in entrees or [] if e.get("date") == x.get("date_precedente")
                   and e.get("categorie") in ("Sabah Espèces", "Banque")]
        explication = ("La clôture %s du %s s'ouvre à %s DT au lieu de %s DT, le fond laissé par %s du %s : une journée "
                       "comptée après le lendemain." % (x.get("cloture"), _jour(x.get("date")), _dt(x.get("ouverture")),
                                                         _dt(x.get("attendu")), x.get("precedente") or "—",
                                                         _jour(x.get("date_precedente")) if x.get("date_precedente") else "—"))
        if le_jour:
            explication += " Ce jour-là, %s DT sont arrivés de « %s » (%s) : la clôture ne les comptait pas en entrée, la " \
                           "journée est tombée en négatif. Sur la carte, ils sont comptés chez le collecteur." % (
                               _dt(sum(_m(e["montant"]) for e in le_jour)), le_jour[0].get("depuis") or "",
                               ", ".join(e["name"] for e in le_jour))
        # Le collecteur principal n'a qu'UNE poche (sa caisse et ce qu'il a collecté) : son saut se corrige sur ce qu'il
        # a collecté — c'est là que l'apport du coffre est compté —, pas sur son fond, qui tomberait en négatif.
        du_principal = bool(caisse_principal) and x.get("caisse") == caisse_principal
        out.append({
            "cle": "saut:%s" % x.get("cloture"), "nature": "Saut de chaîne", "caisse": x.get("caisse"),
            "poche": "Collecte" if du_principal else "Caisse",
            "date": x.get("date"), "document_type": "Cloture Caisse", "document": x.get("cloture"), "montant": saut,
            "titre": "%s : %s DT %s dans sa caisse le %s" % (x.get("caisse"), _dt(abs(saut)),
                                                             "apparus" if saut > 0 else "disparus", _jour(x.get("date"))),
            "explication": explication,
            "options": [
                {"code": "retirer", "effet": _m(-saut),
                 "libelle": ("Cet argent n'existe pas : retirer %s DT de ce qu'il tient sur la carte" % _dt(saut)) if saut > 0
                 else ("Cet argent est bien là : ajouter %s DT à ce qu'il tient sur la carte" % _dt(-saut))},
                {"code": "garder", "effet": 0.0, "libelle": "Ne rien changer au montant (vu, expliqué)"}]})
    for m in entrees_manuelles or []:
        montant = _m(m.get("montant"))
        out.append({
            "cle": "jv:%s" % m.get("name"), "nature": "Entrée manuelle", "caisse": caisse_principal, "poche": "Collecte",
            "date": m.get("date"), "document_type": "Journal Entry", "document": m.get("name"), "montant": montant,
            "titre": "%s DT entrés dans Espèces par une écriture — « %s »" % (_dt(montant), (m.get("remarque") or "").strip() or m.get("name")),
            "explication": "L'écriture %s du %s fait ENTRER %s DT dans « Espèces » depuis « %s »%s, saisie par %s. Un achat "
                           "fait SORTIR de l'argent : si c'en était un, le sens est inversé et la carte compte deux fois "
                           "le montant en trop." % (m.get("name"), _jour(m.get("date")), _dt(montant), m.get("depuis") or "",
                                                    (" (mode de l'écriture : %s)" % m["mode"]) if m.get("mode") else "",
                                                    m.get("saisie_par") or "?"),
            "options": [
                {"code": "inversee", "effet": _m(-2 * montant),
                 "libelle": "C'était une dépense saisie à l'envers : retirer %s DT (deux fois le montant) sur la carte" % _dt(2 * montant)},
                {"code": "entree", "effet": 0.0, "libelle": "C'est bien de l'argent entré en caisse (remboursement reçu…)"}]})
    for a in antidates or []:
        out.append({
            "cle": "antidate:%s" % a.get("name"), "nature": "Paiement antidaté", "caisse": None, "poche": None,
            "date": a.get("saisi_le"), "document_type": "Payment Entry", "document": a.get("name"), "montant": _m(a.get("montant")),
            "titre": "%s DT en espèces saisis le %s mais datés du %s" % (_dt(a.get("montant")), _jour(a.get("saisi_le")), _jour(a.get("date"))),
            "explication": "Le paiement %s (%s, saisi par %s) est compté par la caisse le jour de sa saisie, mais la "
                           "comptabilité le range avant le départ des caisses : il crée un écart entre la carte et le "
                           "compte, pas une erreur d'argent." % (a.get("name"), a.get("client") or "?", a.get("saisi_par") or "?"),
            "options": [{"code": "vu", "effet": 0.0, "libelle": "Vu : l'argent a bien été encaissé, seule la date diffère"}]})
    for p in inattendus or []:
        out.append({
            "cle": "compte:%s" % p.get("name"), "nature": "Compte inattendu", "caisse": None, "poche": None,
            "date": p.get("date"), "document_type": "Payment Entry", "document": p.get("name"), "montant": _m(p.get("montant")),
            "titre": "%s DT « Espèces » enregistrés sur « %s »" % (_dt(p.get("montant")), p.get("compte") or "?"),
            "explication": "Le paiement %s du %s (saisi par %s) est en mode « Espèces » mais posé sur un compte qui n'est ni "
                           "une caisse, ni une banque, ni un partenaire : aucune caisse ne le compte." % (
                               p.get("name"), _jour(p.get("date")), p.get("saisi_par") or "?"),
            "options": [{"code": "vu", "effet": 0.0, "libelle": "Vu : le compte est le bon"}]})
    return out


def trier(anomalies, regs, caisse_principal):
    """(à régulariser, traitées). PURE. Une anomalie est traitée si sa clé a une régularisation, ou — pour la caisse du
    collecteur principal — si un inventaire de sa main est postérieur : il a compté le réel, le passé est absorbé."""
    par_cle = {r["cle"]: r for r in regs or []}
    inventaires = sorted((str(r.get("decide_le") or "")[:10] for r in regs or []
                          if r.get("nature") == INVENTAIRE and r.get("caisse") == caisse_principal), reverse=True)
    dernier = inventaires[0] if inventaires else None
    a_faire, traitees = [], []
    for a in anomalies or []:
        r = par_cle.get(a["cle"])
        if r:
            traitees.append(dict(a, traitee_par=r.get("name"), decision=r.get("decision") or ""))
        elif dernier and a.get("caisse") == caisse_principal and str(a.get("date") or "") <= dernier:
            traitees.append(dict(a, traitee_par=None, decision="Absorbée par l'inventaire du %s" % _jour(dernier)))
        else:
            a_faire.append(a)
    return a_faire, traitees


def effets(regs) -> dict:
    """{(caisse, poche): somme des effets} — ce que les décisions corrigent sur la carte. PURE."""
    out = {}
    for r in regs or []:
        if r.get("caisse") and r.get("poche") in ("Caisse", "Collecte") and _m(r.get("effet")):
            cle = (r["caisse"], r["poche"])
            out[cle] = _m(out.get(cle, 0) + _m(r["effet"]))
    return out


def dernier_inventaire(regs, caisse, poche):
    """Le dernier inventaire de cette poche : {name, date, compte, theorique, ecart, par, motif}, ou None. PURE."""
    liste = sorted((r for r in regs or [] if r.get("nature") in (INVENTAIRE, INVENTAIRE_COFFRE)
                    and r.get("caisse") == caisse and r.get("poche") == poche), key=lambda r: str(r.get("decide_le")), reverse=True)
    if not liste:
        return None
    r = liste[0]
    return {"name": r.get("name"), "date": str(r.get("decide_le") or "")[:10], "compte": _m(r.get("compte")),
            "theorique": _m(r.get("theorique")), "ecart": _m(_m(r.get("compte")) - _m(r.get("theorique"))),
            "par": r.get("decide_par_nom") or "", "motif": r.get("motif") or ""}


def journal(regs) -> list:
    """Le journal des décisions, la plus récente d'abord. PURE."""
    return [{k: r.get(k) for k in ("name", "cle", "nature", "caisse", "poche", "montant", "effet", "decision", "motif",
                                   "document_type", "document", "compte", "theorique", "decide_par_nom")}
            | {"date": str(r.get("decide_le") or "")[:16]}
            for r in sorted(regs or [], key=lambda r: str(r.get("decide_le")), reverse=True)]


# --------------------------------------------------------------------------------------------------------- avec le site
def regularisations(jour=None) -> list:
    """Les régularisations décidées au plus tard le soir de `jour`."""
    if not frappe.db.table_exists(DOCTYPE):
        return []                         # avant la migration : la page marche comme avant
    filtres = {"decide_le": ["<=", "%s 23:59:59" % jour]} if jour else {}
    regs = frappe.get_all(DOCTYPE, filters=filtres, order_by="decide_le asc", fields=[
        "name", "cle", "nature", "caisse", "poche", "date_anomalie", "document_type", "document", "montant", "effet",
        "decision", "motif", "compte", "theorique", "decide_par", "decide_le"])
    for r in regs:
        r["decide_par_nom"] = frappe.utils.get_fullname(r.decide_par) if r.decide_par else ""
    return regs


def peut_regulariser(user=None) -> bool:
    return bool(set(ROLES_DECIDER) & set(frappe.get_roles(user or frappe.session.user)))


def peut_inventorier(cfg=None, user=None) -> bool:
    """Le collecteur principal compte ce qu'il a en main ; la direction peut le saisir avec lui."""
    from customization_app import caisse_collecte as CC
    user = user or frappe.session.user
    return peut_regulariser(user) or user == (cfg or CC.config()).get("responsable")


def _motif(texte, obligatoire=True) -> str:
    texte = " ".join((texte or "").split())
    if obligatoire and len(texte) < MOTIF_MIN:
        frappe.throw(_("Écrivez le motif en quelques mots ({0} caractères au moins) : il reste dans le journal.").format(MOTIF_MIN))
    return texte


def _enregistrer(anomalie, code, motif):
    option = next((o for o in anomalie["options"] if o["code"] == code), None)
    if not option:
        frappe.throw(_("Décision inconnue pour cette anomalie."))
    if frappe.db.exists(DOCTYPE, {"cle": anomalie["cle"]}):
        frappe.throw(_("Cette anomalie est déjà régularisée."))
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "cle": anomalie["cle"], "nature": anomalie["nature"], "caisse": anomalie.get("caisse"),
        "poche": anomalie.get("poche"), "date_anomalie": anomalie.get("date"), "document_type": anomalie.get("document_type"),
        "document": anomalie.get("document"), "montant": anomalie.get("montant"), "effet": option["effet"],
        "decision": option["libelle"], "motif": motif, "decide_par": frappe.session.user,
        "decide_le": frappe.utils.now_datetime()})
    doc.insert(ignore_permissions=True)
    if anomalie.get("document_type") and anomalie.get("document") and frappe.db.exists(anomalie["document_type"], anomalie["document"]):
        frappe.get_doc(anomalie["document_type"], anomalie["document"]).add_comment(
            "Comment", "🧹 Anomalie de caisse régularisée (%s) par %s : %s — %s" % (
                doc.name, frappe.utils.get_fullname(frappe.session.user), option["libelle"], motif))
    return {"name": doc.name, "effet": doc.effet}


@frappe.whitelist(methods=["POST"])
def regulariser(cle, code, motif=None):
    """La direction décide d'une anomalie de la carte : elle quitte la page, la décision reste au journal.
    `cle` : une clé, ou plusieurs (liste JSON) pour décider d'un coup des anomalies de même nature (paiements antidatés)."""
    import json

    from customization_app import argent_liquide as A

    if not peut_regulariser():
        frappe.throw(_("Seule la direction régularise une anomalie de caisse."), frappe.PermissionError)
    motif = _motif(motif)
    cles = json.loads(cle) if isinstance(cle, str) and cle.strip().startswith("[") else [cle]
    # Les anomalies et leurs effets sont RELUS sur le serveur : rien de ce que dit l'écran n'est cru.
    ouvertes = {a["cle"]: a for a in A.situation().get("a_regulariser") or []}
    manquantes = [c for c in cles if c not in ouvertes]
    if not cles or manquantes:
        frappe.throw(_("Cette anomalie n'est plus sur la carte (déjà traitée, ou le document a changé)."))
    return {"faites": [_enregistrer(ouvertes[c], code, motif) for c in cles]}


@frappe.whitelist(methods=["POST"])
def inventorier(especes, note=None, coffre=0):
    """L'INVENTAIRE : le collecteur principal compte les espèces qu'il a en main (sa caisse + ce qu'il a collecté et pas
    versé) ; l'écart avec la carte devient une régularisation et ses anomalies antérieures sont absorbées. `coffre` : le
    comptage de « Sabah Espèces » contre son solde comptable (pour mémoire, sans effet sur la carte)."""
    from customization_app import argent_liquide as A
    from customization_app import caisse_collecte as CC

    cfg = CC.config()
    if not peut_inventorier(cfg):
        frappe.throw(_("L'inventaire se fait par le collecteur principal ou la direction."), frappe.PermissionError)
    try:
        compte = round(float(str(especes).replace(",", ".").replace(" ", "")), 3)
    except (TypeError, ValueError):
        frappe.throw(_("Montant compté illisible."))
    if compte < 0:
        frappe.throw(_("Le montant compté ne peut pas être négatif."))
    maintenant = frappe.utils.now_datetime()
    s = A.situation()
    if frappe.utils.cint(coffre):
        theorique, caisse, poche, nature = _m(s["totaux"]["sabah"]), COFFRE, "Coffre", INVENTAIRE_COFFRE
        effet = 0.0                       # le solde du coffre est comptable : l'écart se corrige par une écriture
    else:
        principal = s.get("principal")
        if not principal:
            frappe.throw(_("Aucun collecteur principal n'est réglé dans Config Caisse."))
        theorique, caisse, poche, nature = _m(principal["especes"]), principal["caisse"], "Collecte", INVENTAIRE
        effet = _m(compte - theorique)
    ecart = _m(compte - theorique)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "cle": "inventaire:%s:%s" % (caisse, maintenant.strftime("%Y%m%d%H%M%S")), "nature": nature,
        "caisse": caisse, "poche": poche, "date_anomalie": maintenant.date(), "montant": ecart, "effet": effet,
        "compte": compte, "theorique": theorique,
        "decision": "Compté %s DT pour %s DT attendus (écart %s%s DT)" % (_dt(compte), _dt(theorique), "+" if ecart > 0 else "", _dt(ecart)),
        "motif": _motif(note, obligatoire=abs(ecart) > 0.0005) or "Inventaire sans écart",
        "decide_par": frappe.session.user, "decide_le": maintenant})
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "compte": compte, "theorique": theorique, "ecart": ecart}


@frappe.whitelist(methods=["POST"])
def annuler(name):
    """Défait une régularisation ou un inventaire (direction) : l'anomalie revient sur la carte."""
    if not peut_regulariser():
        frappe.throw(_("Seule la direction annule une régularisation."), frappe.PermissionError)
    if not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_("Régularisation inconnue."))
    frappe.delete_doc(DOCTYPE, name, ignore_permissions=True)
    return {"annulee": name}


def entree_manuelle(lignes, compte_especes, categorie) -> float:
    """Ce qu'une écriture fait ENTRER dans Espèces depuis des comptes qui ne sont ni le coffre ni une banque. PURE.
    `lignes` : [{account, debit, credit}] ; `categorie` : argent_liquide.categorie. 0 si rien à refuser."""
    entree = sum(_m(l.get("debit")) - _m(l.get("credit")) for l in lignes if l.get("account") == compte_especes)
    autres = [l.get("account") for l in lignes if l.get("account") and l.get("account") != compte_especes]
    if entree <= 0.0005 or (autres and all(categorie(c) in ("Sabah Espèces", "Banque") for c in autres)):
        return 0.0
    return _m(entree)


def controler_ecriture_especes(doc, method=None):
    """Journal Entry › validate. Une écriture saisie À LA MAIN qui fait entrer de l'argent dans « Espèces » depuis un
    compte qui n'est ni le coffre ni une banque est refusée : un achat se saisit par « Dépense » (il fait sortir de
    l'argent), un encaissement par un paiement client. La direction (System Manager) garde la main."""
    from customization_app import argent_liquide as A

    cmd = (getattr(frappe.local, "form_dict", None) or {}).get("cmd") or ""
    if cmd not in SAISIES_MANUELLES or peut_regulariser():
        return
    entree = entree_manuelle([{"account": a.account, "debit": a.debit_in_account_currency, "credit": a.credit_in_account_currency}
                              for a in doc.get("accounts") or []], A.COMPTE_ESPECES, A.categorie)
    if entree:
        frappe.throw(_(
            "Cette écriture fait ENTRER {0} DT dans « Espèces ». Si c'est un achat ou une dépense, l'argent SORT : "
            "saisissez-le par le bouton « Dépense » de la caisse. Si c'est de l'argent reçu d'un client, saisissez un "
            "paiement. Un transfert depuis le coffre ou la banque reste possible ici. Autre cas : demandez à la direction."
        ).format(_dt(entree)), title=_("Entrée d'argent dans Espèces"))
