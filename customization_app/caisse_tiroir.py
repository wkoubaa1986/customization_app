"""Le TIROIR UNIQUE du collecteur principal (demande du 27/11/2026).

LE CONSTAT. La page « Où est l'argent liquide » et le comptage coupaient le collecteur principal (Nejib) en DEUX
poches : « sa caisse » (un fond qui ne se vidait jamais, puisqu'il ne se remet rien à lui-même) et « l'argent
collecté » (d'où sortaient les versements au coffre). Or il n'a qu'UN tiroir, qu'il compte en entier. Depuis le comptage
à l'aveugle (24/11/2026) les deux ne collaient plus : compté 1 800 DT pour 2 871,826 attendus le 26/11 (écart
−1 071,826), compté 2 600 pour 1 912 le 27/11 (+688), alors que le compte « Espèces » disait 1 991,784 puis 2 747,974.
La page affichait « caisse 2 500, collecté −1 964 » : illisible, et faux.

LA RÈGLE. Le tiroir du collecteur principal contient tout l'argent liquide qui n'est ni au coffre, ni en banque, ni chez
un autre employé. Ce qu'il doit avoir se lit donc sur le compte « Espèces » :

    référence = solde du compte « Espèces »
                − ce que tiennent les autres caisses (fond gardé + compté pas encore collecté + pas encore compté)
                − ce que tient un délégué dont la passation n'est pas encore validée
                + les écarts de comptage des autres caisses

(le dernier terme : un employé qui compte 10 DT de moins que prévu ne met pas 10 DT dans le tiroir du collecteur ;
son écart reste le sien). La référence bouge exactement quand de l'argent entre dans le tiroir ou en sort : collecte,
encaissement ou dépense du collecteur, versement au coffre ou en banque.

LA CHAÎNE. Chaque comptage du tiroir (clôture du collecteur, ou vérification par la direction) note la référence du
moment. Le comptage suivant attend :

    attendu = dernier montant compté + (référence de maintenant − référence du dernier comptage)

L'écart d'un comptage ne dit donc que ce qui s'est passé DEPUIS le comptage d'avant ; un vieil écart ne revient pas
chaque jour. Le tout premier comptage (aucune référence notée) se compare directement au compte : compte « Espèces »
moins les autres caisses.

Un comptage est un INSTANTANÉ : il compare ce qu'il y a dans le tiroir MAINTENANT à ce que disent les écritures
MAINTENANT, quelle que soit la date de la journée que la clôture porte. Un versement au coffre se saisit donc avant de
compter (ou le comptage le fait apparaître en écart, rattrapé au comptage suivant).

Les fonctions pures n'utilisent pas `frappe.utils.flt` (hors site, il rend 0).
"""
from __future__ import annotations

import frappe
from frappe import _

#: En dessous, un écart n'est pas signalé dans le verdict de la page (choix du 27/11/2026, non confirmé par lui).
SEUIL_ECART = 20.0
#: Au-delà, une vérification par une autre personne (tiroir, coffre) est à refaire.
JOURS_VERIFICATION = 30
#: Au-delà, le tiroir « n'a pas été compté » (deux jours : le comptage de la veille se fait le matin).
JOURS_SANS_COMPTAGE = 2
CHAMPS_CLOTURE = ("tiroir_unique", "tiroir_reference")


def _m(valeur) -> float:
    try:
        return round(float(valeur or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _dt(valeur) -> str:
    entier, _sep, millimes = f"{abs(_m(valeur)):,.3f}".replace(",", " ").partition(".")
    return f"{'−' if _m(valeur) < 0 else ''}{entier},{millimes}"


def _jour(iso) -> str:
    a, m, j = str(iso)[:10].split("-")
    return f"{j}/{m}/{a}"


# ---------------------------------------------------------------------------------------------------------------- pur
def reference_de(compte, autres, delegues, ecarts_autres) -> dict:
    """La référence du tiroir à un instant. PURE. `compte` : solde du compte « Espèces » ; `autres` : ce que tiennent
    les autres caisses ; `delegues` : ce que tient un délégué avant sa passation ; `ecarts_autres` : la somme des
    écarts de comptage des autres caisses. `absolu` = ce que le tiroir contient d'après le compte seul."""
    absolu = _m(_m(compte) - _m(autres) - _m(delegues))
    return {"compte": _m(compte), "autres": _m(autres), "delegues": _m(delegues), "ecarts_autres": _m(ecarts_autres),
            "absolu": absolu, "reference": _m(absolu + _m(ecarts_autres))}


def attendu_de(base, ref) -> dict:
    """Ce que le tiroir doit contenir. PURE. `base` : le dernier comptage du tiroir {compte, reference, …} ou None ;
    `ref` : reference_de(...) de maintenant. -> {attendu, mode: « chaine » | « compte », delta}."""
    if base and base.get("reference") is not None:
        delta = _m(_m(ref["reference"]) - _m(base["reference"]))
        return {"attendu": _m(_m(base.get("compte")) + delta), "mode": "chaine", "delta": delta}
    return {"attendu": _m(ref["absolu"]), "mode": "compte", "delta": None}


def decomposer(delta, collecte, sorties) -> dict:
    """Le mouvement du tiroir depuis le dernier comptage, en trois lignes lisibles. PURE.
    `collecte` : reçu aux collectes ; `sorties` : versé au coffre, en banque, ailleurs (net) ; le reste = ses propres
    encaissements moins ses dépenses, et tout autre mouvement du compte."""
    return {"collecte": _m(collecte), "sorties": _m(sorties), "autres": _m(_m(delta) - _m(collecte) + _m(sorties)),
            "delta": _m(delta)}


def plus_recent(comptages, jusqu_a=None):
    """Le dernier comptage du tiroir parmi clôtures et vérifications. PURE. `comptages` : [{quand: « AAAA-MM-JJ
    hh:mm:ss », …}] ; `jusqu_a` : borne incluse (même format) pour lire une date passée."""
    valides = [c for c in comptages or [] if c.get("quand") and (not jusqu_a or str(c["quand"]) <= str(jusqu_a))]
    return max(valides, key=lambda c: str(c["quand"])) if valides else None


def derniere_verification(comptages, titulaire, jusqu_a=None):
    """Le dernier comptage du tiroir fait par UNE AUTRE PERSONNE que son titulaire : la seule preuve que l'argent y
    est (un comptage par lui-même est une déclaration). PURE."""
    return plus_recent([c for c in comptages or [] if c.get("par") and c.get("par") != titulaire], jusqu_a)


def depuis_quand(entrees, detient, jour):
    """Depuis quand le tiroir garde de l'argent : ce qu'il contient correspond aux entrées les PLUS RÉCENTES (premier
    entré, premier sorti). PURE. `entrees` : [(date ISO, montant)] ; -> (date de la plus ancienne entrée encore dans
    le tiroir, âge en jours) ou (None, 0) s'il est vide."""
    reste = _m(detient)
    if reste <= 0.5:
        return None, 0
    quand = None
    for date, montant in sorted(((str(d)[:10], _m(m)) for d, m in entrees or []), reverse=True):
        if montant <= 0:
            continue
        quand = date
        reste -= montant
        if reste <= 0.0005:
            break
    return (quand, jours_entre(quand, jour)) if quand else (None, 0)


def jours_entre(ancien, recent) -> int:
    """Nombre de jours entre deux dates ISO (« AAAA-MM-JJ… »). PURE."""
    from datetime import date
    a, b = (date(*map(int, str(x)[:10].split("-"))) for x in (ancien, recent))
    return (b - a).days


def verdict(jour, nom, tiroir, comptage, verification, coffre, pieces, employes, a_regulariser, alertes=(), caisses=None) -> dict:
    """Le verdict de la page, en une phrase et une liste de points. PURE.
    `tiroir` : {attendu, ecart_compte} ; `comptage` : dernier comptage du tiroir {quand, compte, attendu, ecart, par_nom}
    ou None ; `verification` : dernier comptage par une autre personne, ou None ; `coffre` : {solde, inventaire: {date,
    ecart} | None} ; `pieces` : {anciennes: nb, anciennes_total, ecart_comptable} ; `employes` : [{caisse,
    jours_non_comptes}] ; `a_regulariser` : nombre ; `alertes` : plafond, argent gardé trop longtemps.
    -> {etat: « ok » | « a_voir », titre, points: [{niveau: « alerte » | « a_faire », texte, cible}]}"""
    points = []

    def point(niveau, texte, cible=None):
        points.append({"niveau": niveau, "texte": texte, "cible": cible})

    attendu = _m((tiroir or {}).get("attendu"))
    if attendu < -SEUIL_ECART:
        point("alerte", "D'après les écritures, %s a sorti %s DT de plus que ce que son tiroir contenait : son dernier "
                        "comptage ou un versement est à revoir avec lui." % (nom, _dt(-attendu)), "al-principal")
    if comptage:
        ecart = _m(comptage.get("ecart"))
        if abs(ecart) > SEUIL_ECART:
            point("alerte", "Au dernier comptage de son tiroir (%s, par %s), %s : compté %s DT pour %s DT attendus." % (
                _jour(comptage["quand"]), comptage.get("par_nom") or "?",
                ("il manquait %s DT" % _dt(-ecart)) if ecart < 0 else ("il y avait %s DT en trop" % _dt(ecart)),
                _dt(comptage.get("compte")), _dt(comptage.get("attendu"))), "al-principal")
        if jours_entre(comptage["quand"], jour) > JOURS_SANS_COMPTAGE:
            point("a_faire", "Le tiroir de %s n'a pas été compté depuis le %s." % (nom, _jour(comptage["quand"])), "al-principal")
    else:
        point("a_faire", "Le tiroir de %s n'a pas encore été compté en entier : son prochain comptage sera comparé au "
                         "compte « Espèces » et servira de point de départ." % nom, "al-principal")
    if not verification:
        point("a_faire", "Personne d'autre que %s n'a jamais compté son tiroir : ce qu'il déclare n'est pas vérifié." % nom,
              "al-principal")
    elif jours_entre(verification["quand"], jour) > JOURS_VERIFICATION:
        point("a_faire", "Le tiroir de %s n'a pas été vérifié par une autre personne depuis le %s." % (
            nom, _jour(verification["quand"])), "al-principal")
    for a in alertes or ():
        point("alerte", "%s : %s." % (nom, a), "al-principal")
    # `caisses` : {depuis, difference, ecarts_employes} — les caisses validées en face du compte « Espèces ».
    if caisses and abs(_m(caisses.get("difference"))) > SEUIL_ECART:
        d, e, sc, r = (_m(caisses.get(k)) for k in ("difference", "ecarts_employes", "sans_caisse_total", "reste"))
        causes = ([("%s DT d'écarts de comptage des employés" % _dt(e))] if abs(e) > 0.0005 else []) \
            + ([("%s DT de paiements que personne n'a comptés" % _dt(sc))] if abs(sc) > 0.0005 else []) \
            + ([("%s DT sans explication" % _dt(r))] if abs(r) > 0.0005 else [])
        point("alerte", "Depuis le %s, les caisses validées donnent %s DT de %s que le compte « Espèces »%s." % (
            _jour(caisses["depuis"]), _dt(abs(d)), "plus" if d > 0 else "moins",
            (" : " + ", ".join(causes)) if causes else ""), "al-difference")
    solde_coffre = _m((coffre or {}).get("solde"))
    inventaire = (coffre or {}).get("inventaire")
    if not inventaire and abs(solde_coffre) > 0.0005:
        point("a_faire", "Le coffre (%s DT sur le papier) n'a jamais été compté." % _dt(solde_coffre), "al-principal")
    elif inventaire and jours_entre(inventaire["date"], jour) > JOURS_VERIFICATION:
        point("a_faire", "Le coffre (%s DT sur le papier) n'a pas été compté depuis le %s." % (
            _dt(solde_coffre), _jour(inventaire["date"])), "al-principal")
    elif inventaire and abs(_m(inventaire.get("ecart"))) > SEUIL_ECART:
        point("alerte", "Au dernier comptage du coffre (%s), écart de %s DT." % (
            _jour(inventaire["date"]), _dt(inventaire.get("ecart"))), "al-principal")
    if (pieces or {}).get("anciennes"):
        point("alerte", "%d chèque(s) ou traite(s) attendent depuis plus de 30 jours (%s DT) : à pointer un par un." % (
            pieces["anciennes"], _dt(pieces.get("anciennes_total"))), "al-pieces")
    if abs(_m((pieces or {}).get("ecart_comptable"))) > 0.0005:
        point("alerte", "La liste des chèques et traites ne refait pas le solde des comptes d'attente (écart %s DT)."
              % _dt(pieces.get("ecart_comptable")), "al-pieces")
    en_retard = [e["caisse"] for e in employes or [] if (e.get("jours_non_comptes") or 0) > 1]
    if en_retard:
        point("a_faire", "Caisse(s) pas comptée(s) depuis plusieurs jours : %s." % ", ".join(en_retard), "al-personnes")
    if a_regulariser:
        point("a_faire", "%d anomalie(s) attendent votre décision." % a_regulariser, "al-regulariser")
    alertes_nb = sum(1 for p in points if p["niveau"] == "alerte")
    if not points:
        return {"etat": "ok", "titre": "Tout est en ordre.", "points": []}
    return {"etat": "alerte" if alertes_nb else "a_faire",
            "titre": ("%d point(s) à voir" % len(points)) if alertes_nb else ("Rien d'anormal, %d chose(s) à faire" % len(points)),
            "points": sorted(points, key=lambda p: 0 if p["niveau"] == "alerte" else 1)}


# --------------------------------------------------------------------------------------------------------- avec le site
def en_service() -> bool:
    """Les champs existent (après la migration) : avant, tout marche comme avant."""
    return all(frappe.db.has_column("Cloture Caisse", c) for c in CHAMPS_CLOTURE)


def principal(cfg=None):
    """(utilisateur, nom de caisse) du collecteur principal, ou (None, None)."""
    from customization_app import caisse_collecte as CC
    titulaire = (cfg or CC.config()).get("responsable")
    return (titulaire, CC._caisse_de(titulaire)) if titulaire else (None, None)


def est_tiroir(caisse, cfg=None) -> bool:
    """Cette caisse est-elle le tiroir unique du collecteur principal ?"""
    if not caisse or not en_service():
        return False
    return principal(cfg)[1] == caisse


def reference(jour=None, cfg=None, employes=None, collecteurs=None) -> dict:
    """La référence du tiroir au soir de `jour` (aujourd'hui : maintenant). `employes` / `collecteurs` : les listes
    déjà calculées par la page (argent_liquide), pour ne pas les refaire."""
    from customization_app import argent_liquide as A
    from customization_app import caisse_collecte as CC
    from customization_app.caisse_cloture import CAISSE_GLOBALE
    from frappe.utils import flt, getdate, nowdate

    cfg = cfg or CC.config()
    jour = getdate(jour or nowdate())
    titulaire, caisse = principal(cfg)
    depart = getdate(cfg.get("date_depart") or jour)
    if employes is None:
        employes = A._employes(jour, cfg, A.apports(depart, jour), None)
    hors = {caisse, CAISSE_GLOBALE} | set(CC.caisses_sous_traitants()) | set(CC.caisses_partenaires())
    # Ce que TIENNENT les autres, sans la correction d'affichage des régularisations.
    autres = sum(flt(e["total"]) - flt(e.get("regularise")) for e in employes if e["caisse"] not in hors)
    if collecteurs is None:
        collecteurs = A._collecteurs(jour, cfg, set(), None)[0]
    delegues = sum(max(flt(x.get("detient")) - flt(x.get("regularise")), 0) for x in collecteurs if not x.get("principal"))
    ecarts = flt(frappe.db.sql(
        """SELECT SUM(ecart) FROM `tabCloture Caisse` WHERE docstatus < 2 AND caisse NOT IN %s
           AND date_cloture BETWEEN %s AND %s""", (tuple(h for h in hors if h) or ("",), depart, jour))[0][0])
    return reference_de(A._solde(A.COMPTE_ESPECES, jour), autres, delegues, ecarts)


def comptages(caisse, cfg=None) -> list:
    """Tous les comptages du TIROIR, clôtures du collecteur et vérifications de la direction confondues :
    [{source, name, quand, compte, attendu, ecart, reference, par, par_nom}] (ordre quelconque)."""
    if not caisse or not en_service():
        return []
    out = []
    for c in frappe.db.sql(
            """SELECT name, IFNULL(compte_le, creation) AS quand, especes_comptees, fond_conserve, solde_theorique, ecart,
                      tiroir_reference, valide_par, date_cloture
               FROM `tabCloture Caisse` WHERE caisse = %s AND docstatus < 2 AND tiroir_unique = 1""", caisse, as_dict=True):
        reste = c.fond_conserve if c.fond_conserve is not None else c.especes_comptees
        out.append({"source": "Cloture Caisse", "name": c.name, "quand": str(c.quand), "compte": _m(reste),
                    "attendu": _m(c.solde_theorique), "ecart": _m(c.ecart), "reference": c.tiroir_reference,
                    "par": c.valide_par, "journee": str(c.date_cloture)})
    from customization_app import caisse_regularisations as REG
    if frappe.db.table_exists(REG.DOCTYPE) and frappe.db.has_column(REG.DOCTYPE, "tiroir_unique"):
        for r in frappe.get_all(REG.DOCTYPE, filters={"nature": REG.INVENTAIRE, "caisse": caisse, "tiroir_unique": 1},
                                fields=["name", "decide_le", "compte", "theorique", "tiroir_reference", "decide_par"]):
            out.append({"source": REG.DOCTYPE, "name": r.name, "quand": str(r.decide_le), "compte": _m(r.compte),
                        "attendu": _m(r.theorique), "ecart": _m(_m(r.compte) - _m(r.theorique)),
                        "reference": r.tiroir_reference, "par": r.decide_par, "journee": str(r.decide_le)[:10]})
    for c in out:
        c["par_nom"] = frappe.utils.get_fullname(c["par"]) if c.get("par") else ""
    return out


def mouvements_depuis(quand, jour, caisse, cfg=None) -> dict:
    """Ce qui est entré dans le tiroir par les collectes, et ce qui en est sorti par écriture (coffre, banque,
    ailleurs), depuis l'instant `quand` — pour EXPLIQUER le mouvement, pas pour le calculer."""
    from customization_app import argent_liquide as A
    from frappe.utils import flt, getdate

    fin = "%s 23:59:59" % getdate(jour)
    collecte = 0.0
    for r in frappe.db.sql(
            """SELECT especes_recues, especes_remises, validation_seule FROM `tabCloture Caisse`
               WHERE docstatus = 1 AND IFNULL(collecte_par, '') != '' AND IFNULL(passation, '') = ''
                 AND collecte_le > %s AND collecte_le <= %s AND caisse != %s""", (quand, fin, caisse), as_dict=True):
        collecte += A.montant_recu(r)
    collecte += flt(frappe.db.sql(
        """SELECT SUM(especes_recues) FROM `tabPassation Caisse` WHERE docstatus = 1 AND validee_le > %s
           AND validee_le <= %s""", (quand, fin))[0][0])
    # Les apports d'une AUTRE caisse (coffre → la caisse d'un employé) sont entrés chez lui, pas dans le tiroir.
    depart = getdate((cfg or {}).get("date_depart") or jour)
    ailleurs = tuple(a["name"] for a in A.apports(depart, jour) if a["caisse"] != caisse) or ("",)
    sorties = flt(frappe.db.sql(
        """SELECT SUM(jea.credit_in_account_currency - jea.debit_in_account_currency)
           FROM `tabJournal Entry` je JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
           WHERE je.docstatus = 1 AND jea.account = %s AND je.creation > %s AND je.posting_date <= %s
             AND IFNULL(je.cheque_no, '') NOT LIKE 'Dépense caisse%%' AND je.name NOT IN %s""",
        (A.COMPTE_ESPECES, quand, getdate(jour), ailleurs))[0][0])
    return {"collecte": _m(collecte), "sorties": _m(sorties)}


def etat_tiroir(cfg=None, jour=None, employes=None, collecteurs=None):
    """L'état du tiroir du collecteur principal : ce qu'il doit contenir, d'où vient ce chiffre, ses preuves.
    None si aucun collecteur principal n'est réglé (ou avant la migration)."""
    from customization_app import caisse_collecte as CC
    from frappe.utils import getdate, nowdate

    cfg = cfg or CC.config()
    titulaire, caisse = principal(cfg)
    if not titulaire or not caisse or not en_service():
        return None
    jour = getdate(jour or nowdate())
    ref = reference(jour, cfg, employes, collecteurs)
    tous = comptages(caisse, cfg)
    fin = "%s 23:59:59" % jour
    base = plus_recent(tous, fin)
    calcul = attendu_de(base, ref)
    depuis = None
    if calcul["mode"] == "chaine":
        depuis = decomposer(calcul["delta"], **mouvements_depuis(base["quand"], jour, caisse, cfg))
    return {"user": titulaire, "nom": frappe.utils.get_fullname(titulaire) or titulaire, "caisse": caisse,
            "attendu": calcul["attendu"], "mode": calcul["mode"], "reference": ref, "base": base, "depuis": depuis,
            # Ce que dit le compte seul, et la différence : les écarts de comptage jamais passés en comptabilité.
            "selon_compte": ref["absolu"], "ecart_compte": _m(calcul["attendu"] - ref["absolu"]),
            "dernier_comptage": base, "verification": derniere_verification(tous, titulaire, fin)}


# ------------------------------------------------------------------------------- d'après les caisses validées (27/11/2026)
def total_caisses(base, collecte, encaissements, depenses, ecritures, non_compte) -> float:
    """Ce que le collecteur principal doit avoir en additionnant les caisses validées. PURE.
    `base` : le montant de départ figé ; `collecte` : reçu des caisses validées des employés ; `encaissements` /
    `depenses` : ceux de ses propres caisses validées ; `ecritures` : coffre, banque, personnel, autres (net, négatif =
    sorti) ; `non_compte` : ce qu'il a encaissé moins dépensé depuis sa dernière caisse validée."""
    return _m(_m(base) + _m(collecte) + _m(encaissements) - _m(depenses) + _m(ecritures) + _m(non_compte))


def comparer_caisses(total, selon_compte, ecarts_employes, sans_caisse=0.0) -> dict:
    """La différence entre les caisses validées et le compte, et ce qui l'explique. PURE.
    · un employé qui remet plus (ou moins) que son montant attendu met cet écart dans le tiroir sans que le compte ne
      bouge : `ecarts_employes` ;
    · un paiement en espèces qu'aucune caisse validée n'a pris est au compte mais dans aucune caisse : `sans_caisse` ;
    · `reste` : ce que ni l'un ni l'autre n'explique (0 quand tout est expliqué)."""
    difference = _m(_m(total) - _m(selon_compte))
    return {"difference": difference, "ecarts_employes": _m(ecarts_employes), "sans_caisse_total": _m(sans_caisse),
            "reste": _m(difference - _m(ecarts_employes) + _m(sans_caisse))}


def causes_difference(ecarts_detail, sans_caisse, reste) -> list:
    """D'où vient la différence entre les caisses validées et le compte « Espèces », cause par cause. PURE.
    (sa demande du 27/11/2026 : « une partie qui explique d'où viennent les différences entre le plan comptable et les
    caisses espèces »). `effet` : ce que la cause ajoute aux CAISSES par rapport au COMPTE ; la somme des effets est la
    différence. -> [{nature, titre, explication, a_faire, document_type, document, effet}]"""
    out = []
    for e in ecarts_detail or []:
        ecart = _m(e.get("ecart"))
        plus = ecart > 0
        explication = "%s a compté %s DT alors que sa caisse en attendait %s : %s DT de %s. Cet argent %s, " \
                      "mais le compte « Espèces » ne le sait pas." % (
                          e.get("caisse"), _dt(e.get("compte")), _dt(e.get("attendu")), _dt(abs(ecart)),
                          "plus" if plus else "moins",
                          "est passé dans le tiroir du collecteur" if plus else "n'est jamais arrivé chez le collecteur")
        # Seulement si la dépense PEUT expliquer l'écart : un écart d'au moins un dinar, pas plus grand que les dépenses.
        par_depense = plus and ecart >= 1 and ecart <= _m(e.get("depenses")) + 0.005
        if par_depense:
            explication += " Ce jour-là, %s DT de dépenses sont saisies sur sa caisse : une dépense qui n'a pas été " \
                           "payée avec l'argent de cette caisse donne exactement cet effet." % _dt(e.get("depenses"))
        if e.get("note"):
            explication += " Sa note : « %s »." % e["note"]
        if abs(ecart) < 1:
            a_faire = "Rien : un arrondi."
        elif par_depense:
            a_faire = ("Lui demander avec quel argent la dépense a été payée ; si c'est le sien, il faut le lui rembourser, "
                       "si c'est celui d'une autre caisse, la dépense est à déplacer.")
        elif plus:
            a_faire = "Lui demander d'où vient l'argent en trop (un encaissement pas saisi ?) ; sinon c'est un excédent de caisse."
        else:
            a_faire = "Lui demander où est passé l'argent ; sinon c'est un manque à passer en écart de caisse."
        out.append({
            "nature": "Écart de comptage", "effet": ecart, "document_type": "Cloture Caisse", "document": e.get("name"),
            "titre": "Caisse de %s du %s" % (e.get("caisse"), _jour(e.get("date"))), "explication": explication,
            "a_faire": a_faire})
    for m in sans_caisse or []:
        out.append({
            "nature": "Paiement dans aucune caisse", "effet": _m(-_m(m.get("montant"))), "document_type": "Payment Entry",
            "document": m.get("name"),
            "titre": "%s DT de %s, le %s" % (_dt(m.get("montant")), m.get("client") or "?", _jour(m.get("date"))),
            "explication": "Ce paiement en espèces, saisi par %s, est au compte « Espèces » mais aucune caisse validée ne "
                           "l'a compté : personne ne répond de cet argent." % (m.get("saisi_par") or "?"),
            "a_faire": "Savoir qui a reçu l'argent : il doit entrer dans sa caisse (ou le paiement est à corriger)."})
    if abs(_m(reste)) > 0.0005:
        out.append({"nature": "Sans explication", "effet": _m(reste), "document_type": None, "document": None,
                    "titre": "Ce que ni un écart de comptage ni un paiement hors caisse n'explique",
                    "explication": "Une pièce modifiée, annulée ou datée autrement après le comptage de son jour.",
                    "a_faire": "À chercher dans le grand livre du compte « Espèces » depuis le jour de départ."})
    return out


def reglage_caisses():
    """(jour de départ, montant de départ figé) lus dans Config Caisse ; (None, 0.0) tant que le patch n'est pas passé."""
    from frappe.utils import getdate
    lignes = dict(frappe.db.sql("""SELECT field, value FROM `tabSingles` WHERE doctype = 'Config Caisse'
                                   AND field IN ('caisses_validees_depuis', 'caisses_validees_base')"""))
    depuis = lignes.get("caisses_validees_depuis")
    return (getdate(depuis) if depuis else None), _m(lignes.get("caisses_validees_base"))


def _autres_caisses(cfg):
    """Les caisses qui ne sont ni le tiroir du collecteur, ni la globale, ni un sous-traitant, ni un partenaire."""
    from customization_app import caisse_collecte as CC
    from customization_app.caisse_cloture import CAISSE_GLOBALE
    return {principal(cfg)[1], CAISSE_GLOBALE} | set(CC.caisses_sous_traitants()) | set(CC.caisses_partenaires())


def base_a_figer(veille, cfg=None) -> float:
    """Le montant de départ : ce que le collecteur principal avait au soir de `veille` d'après le compte « Espèces »,
    moins le fond que chaque autre caisse avait gardé à son dernier comptage. (L'argent des caisses de ce jour-là ou
    d'avant, collecté après, est compté avec lui : la suite additionne les caisses par leur DATE, pas par le jour de la
    collecte.)"""
    from customization_app import argent_liquide as A
    from customization_app import caisse_collecte as CC
    from frappe.utils import add_days, flt, getdate

    cfg = cfg or CC.config()
    veille = getdate(veille)
    depart = getdate(cfg.get("date_depart") or veille)
    hors = tuple(h for h in _autres_caisses(cfg) if h) or ("",)
    fonds = 0.0
    for caisse in frappe.db.sql_list(
            """SELECT DISTINCT caisse FROM `tabCloture Caisse` WHERE docstatus < 2 AND caisse NOT IN %s
               AND date_cloture BETWEEN %s AND %s""", (hors, depart, veille)):
        derniere = frappe.db.sql(
            """SELECT fond_conserve FROM `tabCloture Caisse` WHERE docstatus < 2 AND caisse = %s AND date_cloture BETWEEN %s AND %s
               ORDER BY date_cloture DESC, creation DESC LIMIT 1""", (caisse, depart, veille))
        fonds += flt(derniere[0][0]) if derniere else 0.0
    # Seulement ce qui était DÉJÀ saisi ce soir-là : un paiement daté d'avant mais saisi après (35 DT datés du 18/11,
    # saisis le 26/11 et comptés par la caisse du 26) est une caisse d'après, pas du montant de départ. Une pièce
    # RÉÉMISE après coup (« …-2 », annulation puis nouvelle saisie) n'est pas de l'argent nouveau : elle reste au départ.
    apres_coup = flt(frappe.db.sql(
        """SELECT SUM(g.debit - g.credit) FROM `tabGL Entry` g
           LEFT JOIN `tabPayment Entry` pe ON g.voucher_type = 'Payment Entry' AND pe.name = g.voucher_no
           LEFT JOIN `tabJournal Entry` je ON g.voucher_type = 'Journal Entry' AND je.name = g.voucher_no
           WHERE g.account = %s AND g.is_cancelled = 0 AND g.posting_date <= %s AND g.creation >= %s
             AND COALESCE(pe.amended_from, je.amended_from) IS NULL""",
        (A.COMPTE_ESPECES, veille, add_days(veille, 1)))[0][0])
    solde = flt(A._solde(A.COMPTE_ESPECES, veille)) - apres_coup
    return _m(solde - fonds)


def selon_caisses(cfg=None, jour=None, non_compte=0.0):
    """Ce que le collecteur principal doit avoir « en remontant les caisses validées » depuis le jour de départ
    (Config Caisse), à partir du montant figé. Les caisses comptent par leur DATE : celle du 25/11 collectée le 26 au
    matin est une caisse du 25. None avant le départ (ou avant le patch).
    -> {depuis, base, collecte, nb_caisses, encaissements, depenses, ecritures, non_compte, total, ecarts_employes}"""
    from customization_app import argent_liquide as A
    from customization_app import caisse_collecte as CC
    from frappe.utils import flt, getdate, nowdate

    cfg = cfg or CC.config()
    depuis, base = reglage_caisses()
    jour = getdate(jour or nowdate())
    caisse = principal(cfg)[1]
    if not depuis or not caisse or jour < depuis:
        return None
    hors = tuple(h for h in _autres_caisses(cfg) if h) or ("",)
    collecte, ecarts, nb, ecarts_detail = 0.0, 0.0, 0, []
    # Les caisses des employés VALIDÉES (comptées par l'employé, reçues par le collecteur) ; celle collectée par un
    # délégué n'entre dans le tiroir qu'une fois sa passation validée.
    for c in frappe.db.sql(
            """SELECT c.name, c.caisse, c.date_cloture, c.especes_recues, c.especes_remises, c.validation_seule, c.ecart,
                      c.solde_theorique, c.especes_comptees, c.depenses_especes, c.note
               FROM `tabCloture Caisse` c
               WHERE c.docstatus = 1 AND c.caisse NOT IN %s AND c.date_cloture BETWEEN %s AND %s
                 AND (IFNULL(c.passation, '') = '' OR EXISTS (SELECT 1 FROM `tabPassation Caisse` p
                                                            WHERE p.name = c.passation AND p.docstatus = 1))""",
            (hors, depuis, jour), as_dict=True):
        collecte += A.montant_recu(c)
        ecarts += flt(c.ecart)
        nb += 1
        if abs(flt(c.ecart)) > 0.0005:
            ecarts_detail.append({"name": c.name, "caisse": c.caisse, "date": str(c.date_cloture), "ecart": _m(c.ecart),
                                  "attendu": _m(c.solde_theorique), "compte": _m(c.especes_comptees),
                                  "depenses": _m(c.depenses_especes), "note": " ".join((c.note or "").split())})
    siennes = frappe.db.sql(
        """SELECT SUM(encaissements_especes + IFNULL(apports_especes, 0)), SUM(depenses_especes), COUNT(*)
           FROM `tabCloture Caisse` WHERE docstatus = 1 AND caisse = %s AND date_cloture BETWEEN %s AND %s""",
        (caisse, depuis, jour))[0]
    # Coffre, banque, dépenses personnelles, autres écritures sur « Espèces » (pas les dépenses de caisse, déjà dans
    # les caisses ; pas les apports à une caisse, entrés par la caisse qui les a reçus).
    depart = getdate(cfg.get("date_depart") or depuis)
    apports = tuple(a["name"] for a in A.apports(depart, jour)) or ("",)
    ecritures = flt(frappe.db.sql(
        """SELECT SUM(jea.debit_in_account_currency - jea.credit_in_account_currency)
           FROM `tabJournal Entry` je JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
           WHERE je.docstatus = 1 AND jea.account = %s AND je.posting_date <= %s
             AND (je.posting_date >= %s OR (je.creation >= %s AND je.amended_from IS NULL))
             AND IFNULL(je.cheque_no, '') NOT LIKE 'Dépense caisse%%' AND je.name NOT IN %s""",
        (A.COMPTE_ESPECES, jour, depuis, depuis, apports))[0][0])
    # Les paiements en espèces d'un jour PASSÉ qu'aucune caisse validée n'a pris : au compte, dans aucune caisse
    # (saisis par un compte sans caisse, ou jour pas encore compté).
    sans_caisse = [{"name": r.name, "date": str(r.posting_date), "montant": _m(r.paid_amount), "client": r.party_name or "",
                    "saisi_par": frappe.utils.get_fullname(r.owner)}
                   for r in frappe.db.sql(
                       """SELECT name, posting_date, paid_amount, party_name, owner FROM `tabPayment Entry`
                          WHERE docstatus = 1 AND payment_type = 'Receive' AND paid_to = %s AND creation >= %s
                            AND posting_date < %s AND IFNULL(custom_cloture_caisse, '') = '' AND amended_from IS NULL
                          ORDER BY posting_date LIMIT 20""",
                       (A.COMPTE_ESPECES, depuis, jour), as_dict=True)]
    out = {"depuis": str(depuis), "base": base, "collecte": _m(collecte), "nb_caisses": nb + int(siennes[2] or 0),
           "encaissements": _m(siennes[0]), "depenses": _m(siennes[1]), "ecritures": _m(ecritures),
           "non_compte": _m(non_compte), "ecarts_employes": _m(ecarts), "sans_caisse": sans_caisse,
           "ecarts_detail": sorted(ecarts_detail, key=lambda e: -abs(e["ecart"]))}
    out["total"] = total_caisses(base, out["collecte"], out["encaissements"], out["depenses"], out["ecritures"], out["non_compte"])
    return out


def champs_cloture(tiroir) -> dict:
    """Ce qu'une clôture du tiroir note pour le comptage suivant."""
    depuis = tiroir.get("depuis") or {}
    return {"tiroir_unique": 1, "tiroir_reference": tiroir["reference"]["reference"],
            "tiroir_base": (tiroir.get("base") or {}).get("name"),
            "tiroir_collecte": depuis.get("collecte"), "tiroir_sorties": depuis.get("sorties")}


def explication(tiroir) -> str:
    """La phrase qui dit d'où vient le montant attendu (fenêtre de comptage, PDF, page)."""
    base = tiroir.get("base")
    if tiroir.get("mode") != "chaine" or not base:
        r = tiroir["reference"]
        return _("Premier comptage du tiroir en entier : le compte « Espèces » ({0} DT) moins ce que tiennent les autres "
                 "caisses ({1} DT).").format(_dt(r["compte"]), _dt(_m(r["autres"] + r["delegues"])))
    d = tiroir["depuis"]
    return _("Compté {0} DT le {1} par {2} ; depuis : collecté {3} DT, versé au coffre ou en banque {4} DT, "
             "encaissements, dépenses et autres mouvements {5} DT.").format(
        _dt(base["compte"]), _jour(base["quand"]), base.get("par_nom") or "?", _dt(d["collecte"]), _dt(d["sorties"]),
        _dt(d["autres"]))
