"""« Où est l'argent liquide » (demande du 24/11/2026) — page /app/argent-liquide.

Toutes les caisses partagent UN compte comptable (« Espèces - A&S ») : la comptabilité ne dit pas qui tient les
billets, et son solde ne recoupe pas les caisses (remises à zéro le 05/10/2026). La page reconstruit donc, à une date,
où se trouve l'argent à partir des FLUX enregistrés depuis la date de départ des caisses (Config Caisse) :

  - chez chaque EMPLOYÉ : le fond gardé à sa dernière clôture, ce qu'il a compté mais pas encore remis (clôture en
    attente de collecte) et ce qui n'est pas encore compté (encaissé − dépensé en espèces depuis sa dernière clôture) ;
  - chez le COLLECTEUR PRINCIPAL (le titulaire, Nejib) : ce qui a été reçu aux collectes (+ passations validées),
    moins ce qui est sorti du compte Espèces par écriture (vers Sabah Espèces, la banque, des dépenses
    personnelles…). Règle du 25/11/2026 (« quand je collecte, je donne à Nejib ; il doit avoir tout ce qui n'est
    pas versé ») : ce que collecte un autre responsable (Wassim) est compté chez lui, avec la mention « remis par » ;
    seul un délégué garde ses collectes tant que sa passation n'est pas validée ;
  - les CHÈQUES et TRAITES pas encore versés (comptes d'attente « Chèques » / « Traite Bancaire ») : chez le
    collecteur principal, ou chez l'employé qui les a reçus tant que sa caisse n'est pas collectée ;
  - sur « Sabah Espèces » : son solde comptable (compte à part) ;
  - en BANQUE : les versements (écritures Espèces / Sabah → Zitouna, BIAT), avec leur référence.

Alerte quand un collecteur garde plus que le plafond de Config Caisse, ou depuis plus de N jours (le plus ancien
argent collecté pas encore couvert par une sortie, premier entré premier sorti). Un compte d'espèces par personne
rendrait tout cela exact dans la comptabilité même (proposé, pas fait).
"""
import re

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, flt, get_datetime, getdate, nowdate

# Les comptes des partenaires : l'argent qu'ils encaissent pour nous (une seule liste, celle des avoirs).
from customization_app.avoir_client import COMPTES_PARTENAIRE

COMPTE_ESPECES = "Espèces - A&S"
COMPTE_SABAH = "Sabah Espèces - A&S"
# Les mêmes que le Server Script « get caisse situation api » (BANQUE_ACCOUNTS, patch caisse_especes_biat).
COMPTES_BANQUE = ("STE430127B - Zitouna - A&S", "BIAT - A&S")
# Les comptes d'attente des pièces reçues pas encore créditées par la banque (le robot bancaire les en sort à
# l'encaissement : il déplace le paid_to du paiement vers la banque).
COMPTES_PORTEFEUILLE = {"Chèques - A&S": "Chèque", "Traite Bancaire - A&S": "Traite"}
PIECE_ANCIENNE_JOURS = 30
JOURS_HISTORIQUE = 14
JOURS_VERSEMENTS = 30
NON_COMPTE_JOURS_MAX = 31
PLAFOND_DEFAUT, JOURS_DEFAUT = 5000.0, 3


def _reglage(champ, defaut):
    ligne = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = %s", champ)
    return defaut if not ligne or ligne[0][0] in (None, "") else flt(ligne[0][0])


def categorie(compte) -> str:
    """Où part (ou d'où vient) l'argent d'une écriture sur le compte Espèces. Fonction pure."""
    if compte == COMPTE_SABAH:
        return "Sabah Espèces"
    if compte in COMPTES_BANQUE:
        return "Banque"
    if "personnel" in (compte or "").lower():
        return "Dépenses personnelles"
    return "Autres"


def apports_depuis():
    """Le jour à partir duquel un apport d'espèces entre dans la journée de caisse de celui qui le saisit (Config
    Caisse, posé à la mise en route — patch caisse_apports_regularisations). None : pas encore en service."""
    ligne = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = 'apports_depuis'")
    return getdate(ligne[0][0]) if ligne and ligne[0][0] else None


def apports(debut, fin):
    """Les APPORTS d'espèces à une caisse (26/11/2026) : une écriture qui fait ENTRER de l'argent dans « Espèces »
    depuis le coffre (« Sabah Espèces ») ou une banque, saisie par le titulaire d'une caisse. Le 19/10/2026, 6 000 DT
    sortis du coffre pour payer des billets d'avion n'étaient pas une entrée pour la clôture : la journée est tombée à
    −3 289 DT et la chaîne des clôtures a sauté. Depuis `apports_depuis`, l'apport est une entrée de la journée de CETTE
    caisse (au plus tard des deux : sa date, ou le jour où il a été saisi) et n'est plus compté chez le collecteur.
    -> [{name, date, montant, caisse, depuis, saisie_par}]"""
    depuis = apports_depuis()
    if not depuis or getdate(fin) < depuis:
        return []
    from customization_app import caisse_collecte as CC
    from customization_app.caisse_cloture import CAISSE_GLOBALE
    lignes = frappe.db.sql(
        """SELECT je.name, GREATEST(je.posting_date, DATE(je.creation)) AS jour, je.owner,
                  SUM(CASE WHEN jea.account = %(esp)s THEN jea.debit_in_account_currency - jea.credit_in_account_currency ELSE 0 END) AS montant,
                  GROUP_CONCAT(DISTINCT CASE WHEN jea.account != %(esp)s THEN jea.account END) AS contre
           FROM `tabJournal Entry` je JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
           WHERE je.docstatus = 1 AND je.posting_date >= %(depuis)s
             AND GREATEST(je.posting_date, DATE(je.creation)) BETWEEN %(d)s AND %(f)s
             AND IFNULL(je.cheque_no, '') NOT LIKE 'Dépense caisse%%'
           GROUP BY je.name HAVING montant > 0 ORDER BY jour, je.creation""",
        {"esp": COMPTE_ESPECES, "depuis": depuis, "d": max(getdate(debut), depuis), "f": fin}, as_dict=True)
    if not lignes:
        return []
    valides = set(CC.noms_caisses()) - {CAISSE_GLOBALE} - set(CC.caisses_partenaires())
    caisse_de, out = {}, []
    for l in lignes:
        comptes = [c for c in (l.contre or "").split(",") if c]
        if not comptes or any(categorie(c) not in ("Sabah Espèces", "Banque") for c in comptes):
            continue
        if l.owner not in caisse_de:
            caisse_de[l.owner] = CC._caisse_de(l.owner)
        if caisse_de[l.owner] not in valides:
            continue
        out.append({"name": l.name, "date": str(l.jour), "montant": flt(l.montant, 3), "caisse": caisse_de[l.owner],
                    "depuis": comptes[0].replace(" - A&S", ""), "saisie_par": frappe.utils.get_fullname(l.owner)})
    return out


def anciennete(entrees, sorties_total, jour):
    """Premier entré, premier sorti : la date du plus ancien argent collecté encore détenu, et son âge en jours.
    `entrees` : [(date, montant)] dans l'ordre. (None, 0) si tout est sorti. Fonction pure."""
    reste = flt(sorties_total)
    for quand, montant in entrees:
        if reste >= flt(montant) - 0.0005:
            reste -= flt(montant)
            continue
        return quand, date_diff(jour, quand)
    return None, 0


def alertes_collecteur(detient, plafond, jours_max, depuis, age) -> list:
    """Les alertes d'un collecteur : au-delà du plafond, ou de l'argent gardé trop longtemps. `detient` : ce qu'il a en
    main — pour le collecteur principal, le chiffre de RÉFÉRENCE (compte « Espèces » − autres caisses)."""
    alertes = []
    if plafond and flt(detient) > flt(plafond) + 0.0005:
        alertes.append(_("garde {0} DT, au-delà du plafond de {1} DT").format(_fmt(detient), _fmt(plafond)))
    if jours_max and flt(detient) > 0.5 and depuis and age > jours_max:
        alertes.append(_("de l'argent collecté le {0} n'est toujours pas versé ({1} jours)").format(
            getdate(depuis).strftime("%d/%m/%Y"), age))
    return alertes


def montant_recu(cloture) -> float:
    """Ce que le collecteur a reçu d'une clôture collectée. Validée par lui-même (« validation directe » de la caisse
    d'un absent, ou sa propre caisse) : « espèces reçues » n'est pas rempli, l'argent remis est chez lui quand même
    (2 480,688 DT oubliés avant le 24/11/2026). Fonction pure."""
    if flt(cloture.get("especes_recues")):
        return flt(cloture.get("especes_recues"), 3)
    return flt(cloture.get("especes_remises"), 3) if cloture.get("validation_seule") else 0.0


def fond_de(cloture) -> float:
    """Le fond qu'une clôture laisse à la suivante (même règle que caisse_cloture._ouverture). Fonction pure."""
    if cloture.get("fond_conserve") is not None:
        return flt(cloture.get("fond_conserve"), 3)
    base = cloture.get("especes_comptees") if cloture.get("especes_comptees") is not None else cloture.get("solde_theorique")
    return flt(flt(base) - flt(cloture.get("especes_remises")), 3)


def sauts_de_chaine(clotures) -> list:
    """Les clôtures dont l'ouverture n'est pas le fond de la clôture précédente de la même caisse (ordre des dates).
    Arrive quand un jour est compté AVANT la veille (l'ouverture ne lit que les clôtures déjà validées) : CLO-1055 du
    20/10/2026 ouvrait à 2 003,782 DT en ignorant le 19/10 (−3 289,218 DT), +5 293 DT apparus chez Nejib Koubaa.
    `clotures` : dans l'ordre (caisse, date, création). Fonction pure."""
    sauts, precedente = [], {}
    for c in clotures:
        p = precedente.get(c["caisse"])
        attendu = fond_de(p) if p else 0.0
        if abs(flt(c.get("solde_ouverture")) - attendu) > 0.0005:
            sauts.append({"caisse": c["caisse"], "date": str(c["date_cloture"]), "cloture": c["name"],
                          "precedente": p["name"] if p else None, "date_precedente": str(p["date_cloture"]) if p else None,
                          "attendu": attendu, "ouverture": flt(c.get("solde_ouverture"), 3),
                          "saut": flt(flt(c.get("solde_ouverture")) - attendu, 3)})
        precedente[c["caisse"]] = c
    return sauts


def detenteur_piece(saisie_par, employes_actifs, responsables, collectee) -> str | None:
    """Chez qui est un chèque / une traite pas encore versé. Fonction pure.
    `saisie_par` : l'utilisateur qui l'a saisi ; `employes_actifs` : {utilisateur: nom de caisse} ;
    `responsables` : les collecteurs titulaires (leur argent est celui du principal) ; `collectee` : la caisse de
    l'employé a été collectée APRÈS l'entrée de la pièce. → le nom de la caisse de l'employé qui l'a encore, ou
    None = chez le collecteur principal (saisie par un responsable, le robot, un compte sans caisse, ou collectée)."""
    if saisie_par in responsables or saisie_par not in employes_actifs or collectee:
        return None
    return employes_actifs[saisie_par]


def reference(remarque, cheque_no=None) -> str:
    """« Ref : TT263168Q77X » ou « réf. TT26306750847374 » dans la remarque d'un versement. Fonction pure."""
    m = re.search(r"r[ée]f\.?\s*:?\s*([A-Z0-9][A-Z0-9-]{5,})", remarque or "", re.I)
    return m.group(1) if m else (cheque_no or "")


def _employes(jour, cfg, apports_liste=(), effets=None):
    from customization_app import caisse_collecte as CC
    from customization_app import caisse_cloture as CL
    from customization_app.rapport_caisse_journaliere import get_data
    # Employés partis : leur caisse n'encaisse plus, on ne recalcule pas un mois de « non compté » pour rien.
    partis = set(frappe.get_all("Employee", filters={"status": ["!=", "Active"]}, pluck="employee_name"))
    # Partenaires (Economic Aqua Solution) : ils encaissent sur LEUR compte, leur caisse ne se compte pas (demande du
    # 24/11/2026) — ce qui part chez eux est montré à part (_partenaires).
    partenaires = CC.caisses_partenaires()
    caisses, remises_en_attente = [], {}
    for caisse in CC.noms_caisses():
        if caisse == CL.CAISSE_GLOBALE or caisse in partenaires:
            continue
        depart = getdate(CC.date_depart_pour(caisse, cfg) or cfg.get("date_depart") or jour)
        clotures = frappe.db.sql(
            """SELECT name, date_cloture, statut, docstatus, especes_remises, fond_conserve
               FROM `tabCloture Caisse` WHERE caisse = %s AND date_cloture BETWEEN %s AND %s AND docstatus < 2
               ORDER BY date_cloture DESC, creation DESC""", (caisse, depart, jour), as_dict=True)
        derniere = clotures[0] if clotures else None
        # Un employé peut compter plusieurs jours avant la collecte : toutes les remises attendent encore
        # chez lui, même si une clôture plus récente existe. Seul le fond vient du dernier comptage.
        remises_en_attente[caisse] = flt(sum(flt(c.especes_remises) for c in clotures
                                           if c.docstatus == 0 and c.statut in CC.STATUTS_EN_ATTENTE), 3)
        # Pas encore compté : depuis le lendemain de la dernière clôture (ou la date de départ) jusqu'au jour.
        debut = getdate(add_days(derniere.date_cloture, 1) if derniere else depart)
        debut = max(debut, getdate(add_days(jour, -NON_COMPTE_JOURS_MAX)))
        caisses.append((caisse, derniere, debut if debut <= jour and caisse not in partis else None))
    # UN calcul du rapport par date de début (toutes les caisses d'un coup : aussi rapide que pour une seule).
    non_comptes = {}
    for debut in sorted({d for _c, _x, d in caisses if d}):
        data = get_data(str(debut), str(jour), employe="")
        par_emp = {e["employe"]: e.get("par_mode") or {} for e in (data.get("recap") or {}).get("par_employe") or []}
        lignes_dep = (data.get("depenses") or {}).get("lignes") or []
        for caisse, _x, d in caisses:
            if d != debut:
                continue
            especes = flt(par_emp.get(caisse, {}).get("Espèces"))
            depenses = flt(sum(flt(l["montant"]) for l in lignes_dep
                               if l.get("mode") == "Espèces" and (l.get("caisse") or l.get("saisi_par")) == caisse))
            # Un apport (coffre ou banque → cette caisse) pas encore pris par une clôture : il est dans le tiroir.
            recus = flt(sum(a["montant"] for a in apports_liste if a["caisse"] == caisse and getdate(a["date"]) >= debut))
            non_comptes[caisse] = (flt(especes + recus - depenses, 3),
                                   (date_diff(jour, debut) + 1) if (especes or depenses or recus) else 0, flt(recus, 3))
    lignes = []
    for caisse, derniere, _d in caisses:
        fond = flt(derniere.fond_conserve, 3) if derniere else 0.0
        a_remettre = remises_en_attente[caisse]
        non_compte, jours_non_comptes, apports_attente = non_comptes.get(caisse, (0.0, 0, 0.0))
        # Régularisations décidées sur cette caisse (saut de chaîne retiré…) : elles corrigent le fond affiché.
        regul = flt((effets or {}).get((caisse, "Caisse")), 3)
        fond = flt(fond + regul, 3)
        total = flt(fond + a_remettre + non_compte, 3)
        if not derniere and not total:
            continue                      # caisse jamais utilisée (assistants…)
        lignes.append({"caisse": caisse, "fond": fond, "a_remettre": a_remettre, "non_compte": non_compte,
                       "jours_non_comptes": jours_non_comptes, "total": total, "regularise": regul,
                       "apports_attente": apports_attente,
                       "derniere": ({"name": derniere.name, "date": str(derniere.date_cloture), "statut": derniere.statut}
                                    if derniere else None)})
    return lignes


def _collecteurs(jour, cfg, hors=(), effets=None):
    """Ce que détient le collecteur principal : reçu aux collectes + passations validées − sorties du compte Espèces.
    Tout va chez lui (règle du 25/11/2026), sauf l'argent d'une passation pas encore validée, chez son délégué."""
    from customization_app import caisse_collecte as CC
    depart = getdate(cfg.get("date_depart") or jour)
    fin = "%s 23:59:59" % jour
    titulaire = cfg.get("responsable")
    collecteurs = {}

    def c(user):
        return collecteurs.setdefault(user, {"user": user, "nom": frappe.utils.get_fullname(user) or user,
                                             "caisse": CC._caisse_de(user), "recu": 0.0, "passations": 0.0,
                                             "sorties": {}, "entrees_autres": 0.0, "detail_sorties": [], "_entrees": [],
                                             "_remis_par": {}, "principal": user == titulaire})

    def chez_principal(user, montant):
        """Le collecteur principal reçoit ; « remis par » garde la trace de qui avait collecté."""
        x = c(titulaire or user)
        if user and user != x["user"]:
            m = x["_remis_par"].setdefault(user, {"nom": frappe.utils.get_fullname(user) or user, "montant": 0.0, "nb": 0})
            m["montant"] = flt(m["montant"] + montant, 3)
            m["nb"] += 1
        return x

    if titulaire:
        c(titulaire)
    for r in frappe.db.sql(
            """SELECT collecte_par, collecte_le, especes_recues, especes_remises, validation_seule FROM `tabCloture Caisse`
               WHERE docstatus = 1 AND IFNULL(collecte_par, '') != '' AND date_cloture >= %s AND collecte_le <= %s
                 AND IFNULL(passation, '') = '' ORDER BY collecte_le""", (depart, fin), as_dict=True):
        recu = montant_recu(r)
        if recu:
            x = chez_principal(r.collecte_par, recu)
            x["recu"] += recu
            x["_entrees"].append((getdate(r.collecte_le), recu))
    # Passations : le délégué a collecté (ses clôtures portent la passation), le principal reçoit à la validation.
    for r in frappe.db.sql(
            """SELECT delegue, responsable, validee_le, especes_recues FROM `tabPassation Caisse`
               WHERE docstatus = 1 AND validee_le BETWEEN %s AND %s""", ("%s 00:00:00" % depart, fin), as_dict=True):
        if flt(r.especes_recues):
            x = chez_principal(r.delegue, flt(r.especes_recues))
            x["passations"] += flt(r.especes_recues)
            x["_entrees"].append((getdate(r.validee_le), flt(r.especes_recues)))
    # Passation pas encore validée (au soir choisi) : l'argent de ses collectes est encore chez le délégué.
    for r in frappe.db.sql(
            """SELECT p.delegue, c.collecte_le, c.especes_recues, c.especes_remises, c.validation_seule
               FROM `tabPassation Caisse` p JOIN `tabCloture Caisse` c ON c.passation = p.name AND c.docstatus = 1
               WHERE p.docstatus < 2 AND (p.docstatus = 0 OR p.validee_le > %s)
                 AND c.date_cloture >= %s AND c.collecte_le <= %s ORDER BY c.collecte_le""", (fin, depart, fin), as_dict=True):
        recu = montant_recu(r)
        if recu and r.delegue:
            x = c(r.delegue)
            x["recu"] += recu
            x["_entrees"].append((getdate(r.collecte_le), recu))
    # Sorties (et retours) du compte Espèces qui ne sont pas des dépenses de caisse.
    for r in frappe.db.sql(
            """SELECT je.name, je.posting_date, je.owner, je.cheque_no, je.user_remark, je.mode_of_payment,
                      SUM(CASE WHEN jea.account = %(esp)s THEN jea.credit_in_account_currency - jea.debit_in_account_currency ELSE 0 END) AS sortie,
                      GROUP_CONCAT(DISTINCT CASE WHEN jea.account != %(esp)s THEN jea.account END) AS contre
               FROM `tabJournal Entry` je JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
               WHERE je.docstatus = 1 AND je.posting_date BETWEEN %(d)s AND %(j)s
                 AND IFNULL(je.cheque_no, '') NOT LIKE 'Dépense caisse%%'
               GROUP BY je.name HAVING SUM(CASE WHEN jea.account = %(esp)s THEN 1 ELSE 0 END) > 0
               ORDER BY je.posting_date, je.creation""",
            {"esp": COMPTE_ESPECES, "d": depart, "j": jour}, as_dict=True):
        if r.name in hors:
            continue                      # un apport à une caisse : il est dans la journée de cette caisse (apports)
        # Toute sortie du compte Espèces est prise sur l'argent du principal, quel que soit celui qui l'a saisie.
        qui = titulaire or (r.owner if r.owner in collecteurs else None)
        if not qui:
            continue
        x = c(qui)
        cat = categorie((r.contre or "").split(",")[0])
        if flt(r.sortie) >= 0:
            x["sorties"][cat] = flt(x["sorties"].get(cat)) + flt(r.sortie)
        else:
            x["entrees_autres"] += -flt(r.sortie)
            x["_entrees"].append((getdate(r.posting_date), -flt(r.sortie)))
            # Une ENTRÉE d'argent qui ne vient ni de Sabah ni de la banque (25/11/2026) : « Achat divers Néjib » faisait
            # entrer 76,6 et 80 DT dans Espèces — un achat fait sortir de l'argent, le sens est peut-être inversé.
            if cat not in ("Sabah Espèces", "Banque"):
                x.setdefault("entrees_manuelles", []).append({
                    "name": r.name, "date": str(r.posting_date), "montant": flt(-flt(r.sortie), 3),
                    "depuis": (r.contre or "").replace(" - A&S", ""), "remarque": reference(r.user_remark, r.cheque_no) or "",
                    "mode": r.mode_of_payment or "", "saisie_par": frappe.utils.get_fullname(r.owner)})
            x.setdefault("entrees_detail", []).append({
                "name": r.name, "date": str(r.posting_date), "montant": flt(-flt(r.sortie), 3), "categorie": cat,
                "depuis": (r.contre or "").replace(" - A&S", ""), "remarque": (r.cheque_no or r.user_remark or "").strip()})
        x["detail_sorties"].append({"name": r.name, "date": str(r.posting_date), "montant": flt(r.sortie, 3),
                                    "vers": r.contre or "", "categorie": cat, "saisie_par": frappe.utils.get_fullname(r.owner),
                                    "reference": reference(r.user_remark, r.cheque_no)})
    plafond = _reglage("plafond_collecteur", PLAFOND_DEFAUT)
    jours_max = int(_reglage("jours_plafond_collecteur", JOURS_DEFAUT))
    out = []
    for x in collecteurs.values():
        sorties = flt(sum(x["sorties"].values()), 3)
        x["sorties_total"] = sorties
        # Régularisations et inventaire (caisse_regularisations) : l'écart constaté corrige ce qu'il doit détenir.
        x["regularise"] = flt((effets or {}).get((x["caisse"], "Collecte")), 3)
        x["detient"] = flt(x["recu"] + x["passations"] + x["entrees_autres"] - sorties + x["regularise"], 3)
        depuis, age = anciennete(sorted(x.pop("_entrees")), sorties, jour)
        x["depuis"], x["age_jours"] = (str(depuis) if depuis else None), age
        x["alertes"] = alertes_collecteur(x["detient"], plafond, jours_max, depuis, age)
        x["remis_par"] = sorted(x.pop("_remis_par").values(), key=lambda m: -m["montant"])
        x["detail_sorties"] = x["detail_sorties"][-15:][::-1]
        if x["recu"] or x["passations"] or sorties or x["entrees_autres"] or x["regularise"]:
            out.append(x)
    return sorted(out, key=lambda x: -x["detient"]), plafond, jours_max


def _fmt(v) -> str:
    return "{:,.3f}".format(flt(v)).replace(",", " ").replace(".", ",")


def _solde(compte, jour):
    return flt(frappe.db.sql("""SELECT SUM(debit - credit) FROM `tabGL Entry`
                                WHERE account = %s AND is_cancelled = 0 AND posting_date <= %s""", (compte, jour))[0][0], 3)


def _versements(jour):
    """Les versements en banque des 30 derniers jours : écritures qui créditent Espèces ou Sabah et débitent une banque."""
    return frappe.db.sql(
        """SELECT je.name, je.posting_date, je.owner, je.user_remark, je.cheque_no,
                  SUM(CASE WHEN jea.account IN %(caisses)s THEN jea.credit_in_account_currency ELSE 0 END) AS montant,
                  GROUP_CONCAT(DISTINCT CASE WHEN jea.account IN %(caisses)s AND jea.credit_in_account_currency > 0 THEN jea.account END) AS depuis,
                  GROUP_CONCAT(DISTINCT CASE WHEN jea.account IN %(banques)s AND jea.debit_in_account_currency > 0 THEN jea.account END) AS banque
           FROM `tabJournal Entry` je JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
           WHERE je.docstatus = 1 AND je.posting_date BETWEEN %(d)s AND %(j)s
           GROUP BY je.name
           HAVING montant > 0 AND banque IS NOT NULL
           ORDER BY je.posting_date DESC""",
        {"caisses": (COMPTE_ESPECES, COMPTE_SABAH), "banques": COMPTES_BANQUE,
         "d": add_days(jour, -JOURS_VERSEMENTS), "j": jour}, as_dict=True)


def _jour_par_jour(jour):
    """Les flux des 14 derniers jours : encaissé / dépensé (clôtures), collecté, vers Sabah, vers la banque, autres."""
    debut = add_days(jour, -(JOURS_HISTORIQUE - 1))
    from customization_app.caisse_cloture import CAISSE_GLOBALE
    jours = {str(add_days(debut, i)): {"date": str(add_days(debut, i)), "encaisse": 0.0, "depense": 0.0, "collecte": 0.0,
                                       "sabah": 0.0, "banque": 0.0, "autres": 0.0, "non_comptees": 0}
             for i in range(JOURS_HISTORIQUE)}
    for r in frappe.db.sql("""SELECT date_cloture, SUM(encaissements_especes) e, SUM(depenses_especes) d FROM `tabCloture Caisse`
                              WHERE docstatus < 2 AND caisse != %s AND date_cloture BETWEEN %s AND %s GROUP BY date_cloture""",
                           (CAISSE_GLOBALE, debut, jour), as_dict=True):
        jours[str(r.date_cloture)].update({"encaisse": flt(r.e, 3), "depense": flt(r.d, 3)})
    for r in frappe.db.sql("""SELECT DATE(collecte_le) j, SUM(especes_recues) s FROM `tabCloture Caisse`
                              WHERE docstatus = 1 AND collecte_le BETWEEN %s AND %s GROUP BY DATE(collecte_le)""",
                           ("%s 00:00:00" % debut, "%s 23:59:59" % jour), as_dict=True):
        if str(r.j) in jours:
            jours[str(r.j)]["collecte"] = flt(r.s, 3)
    for r in frappe.db.sql(
            """SELECT je.posting_date,
                      SUM(CASE WHEN jea.account = %(esp)s THEN jea.credit_in_account_currency - jea.debit_in_account_currency ELSE 0 END) AS sortie,
                      GROUP_CONCAT(DISTINCT CASE WHEN jea.account != %(esp)s THEN jea.account END) AS contre
               FROM `tabJournal Entry` je JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
               WHERE je.docstatus = 1 AND je.posting_date BETWEEN %(d)s AND %(j)s AND IFNULL(je.cheque_no, '') NOT LIKE 'Dépense caisse%%'
               GROUP BY je.name HAVING SUM(CASE WHEN jea.account = %(esp)s THEN 1 ELSE 0 END) > 0""",
            {"esp": COMPTE_ESPECES, "d": debut, "j": jour}, as_dict=True):
        cat = categorie((r.contre or "").split(",")[0])
        cle = {"Sabah Espèces": "sabah", "Banque": "banque"}.get(cat, "autres")
        jours[str(r.posting_date)][cle] += flt(r.sortie, 3)
    for v in _versements(jour):
        if (v.depuis or "") == COMPTE_SABAH and str(v.posting_date) in jours:
            jours[str(v.posting_date)]["banque"] += flt(v.montant, 3)     # Sabah → banque
    return [jours[k] for k in sorted(jours, reverse=True)]


def _rapprochement(jour, cfg, employes, collecteurs, effets=None):
    """Pourquoi l'argent reconstitué n'est pas le solde du compte Espèces. Décomposition EXACTE de l'écart :
    reconstitué − mouvements du compte depuis le départ
      = (encaissé selon les clôtures − entré au compte par paiements)
      − (dépensé selon les clôtures − sorti du compte en dépenses)
      + écarts de comptage + écarts de remise + sauts de chaîne + pas encore compté (+ reste, ≈ 0).
    Les transferts (Sabah, banque, perso) sont les mêmes des deux côtés et s'annulent."""
    from customization_app import caisse_collecte as CC
    from customization_app.caisse_cloture import CAISSE_GLOBALE
    depart = getdate(cfg.get("date_depart") or jour)
    gl = lambda cond, *v: flt(frappe.db.sql(  # noqa: E731
        "SELECT SUM(debit - credit) FROM `tabGL Entry` WHERE account = %s AND is_cancelled = 0 AND " + cond,
        (COMPTE_ESPECES,) + v)[0][0], 3)
    avant = gl("posting_date < %s", depart)
    au_jour = gl("posting_date <= %s", jour)
    pe = frappe.db.sql("""SELECT SUM(debit), SUM(credit) FROM `tabGL Entry` WHERE account = %s AND is_cancelled = 0
                          AND voucher_type = 'Payment Entry' AND posting_date BETWEEN %s AND %s""", (COMPTE_ESPECES, depart, jour))[0]
    dep_caisse = flt(frappe.db.sql(
        """SELECT SUM(g.credit - g.debit) FROM `tabGL Entry` g JOIN `tabJournal Entry` je ON je.name = g.voucher_no
           WHERE g.account = %s AND g.is_cancelled = 0 AND g.voucher_type = 'Journal Entry'
             AND je.cheque_no LIKE 'Dépense caisse%%' AND g.posting_date BETWEEN %s AND %s""", (COMPTE_ESPECES, depart, jour))[0][0], 3)
    entre_paiements, sorti_paiements = flt(pe[0], 3), flt(pe[1], 3)
    mouvements = flt(au_jour - avant, 3)
    # Les clôtures de chaque caisse depuis SA date de départ.
    departs = {}
    clotures = []
    for c in frappe.db.sql(
            """SELECT name, caisse, date_cloture, solde_ouverture, encaissements_especes, depenses_especes, ecart,
                      apports_especes, especes_comptees, especes_remises, fond_conserve, solde_theorique, especes_recues, validation_seule,
                      docstatus, IFNULL(collecte_par, '') AS collecte_par
               FROM `tabCloture Caisse` WHERE docstatus < 2 AND caisse != %s AND date_cloture BETWEEN %s AND %s
               ORDER BY caisse, date_cloture, creation""", (CAISSE_GLOBALE, depart, jour), as_dict=True):
        if c.caisse not in departs:
            departs[c.caisse] = getdate(CC.date_depart_pour(c.caisse, cfg) or depart)
        if getdate(c.date_cloture) >= departs[c.caisse]:
            clotures.append(c)
    encaisse = flt(sum(flt(c.encaissements_especes) for c in clotures), 3)
    depense = flt(sum(flt(c.depenses_especes) for c in clotures), 3)
    ecarts_comptage = flt(sum(flt(c.ecart) for c in clotures), 3)
    ecarts_remise = flt(sum(montant_recu(c) - flt(c.especes_remises) for c in clotures
                            if c.docstatus == 1 and c.collecte_par), 3)
    sauts = sauts_de_chaine(clotures)
    total_sauts = flt(sum(s["saut"] for s in sauts), 3)
    # Un apport pas encore pris par une clôture est déjà au compte (par son écriture) : il n'explique aucun écart.
    non_compte = flt(sum(flt(e["non_compte"]) - flt(e.get("apports_attente")) for e in employes), 3)
    reconstitue = flt(sum(flt(e["total"]) for e in employes) + sum(flt(x["detient"]) for x in collecteurs), 3)
    # Apports (coffre / banque → une caisse) pris par les clôtures : au compte par écriture, dans le fond par la clôture.
    apports_clotures = flt(sum(flt(c.get("apports_especes")) for c in clotures), 3)
    regularisations = flt(sum((effets or {}).values()), 3)
    termes = {
        "encaisse": flt(encaisse - entre_paiements, 3),
        "depense": flt(-(depense - sorti_paiements - dep_caisse), 3),
        "ecarts_comptage": ecarts_comptage, "ecarts_remise": ecarts_remise, "sauts": total_sauts, "non_compte": non_compte,
        # Ce que la direction a décidé de corriger sur la carte (saut retiré, inventaire…).
        "regularisations": regularisations,
    }
    ecart = flt(reconstitue - mouvements, 3)
    # Paiements « Espèces » posés ailleurs que sur le compte Espèces (hors partenaires). Sur un compte de BANQUE, ce
    # n'est pas une erreur (sa précision du 25/11/2026 : « c'est moi qui le choisis, ce sont des versements en espèces
    # sur le compte ») : l'argent est en banque, pas dans un tiroir. Sur un autre compte : à regarder.
    verses_banque, mal_comptabilises = [], []
    for r in frappe.db.sql(
            """SELECT pe.name, pe.posting_date, pe.paid_to, pe.paid_amount, pe.owner, pe.party_name, pe.reference_no, a.account_type
               FROM `tabPayment Entry` pe LEFT JOIN `tabAccount` a ON a.name = pe.paid_to
               WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND pe.mode_of_payment = 'Espèces'
                 AND pe.paid_to != %s AND pe.paid_to NOT IN %s AND pe.posting_date BETWEEN %s AND %s
                 AND IFNULL(a.account_type, '') != 'Cash'
               ORDER BY pe.posting_date""", (COMPTE_ESPECES, COMPTES_PARTENAIRE, depart, jour), as_dict=True):
        ligne = {"name": r.name, "date": str(r.posting_date), "compte": (r.paid_to or "").replace(" - A&S", ""),
                 "montant": flt(r.paid_amount, 3), "saisi_par": frappe.utils.get_fullname(r.owner),
                 "client": r.party_name or "", "reference": r.reference_no or ""}
        (verses_banque if r.account_type == "Bank" else mal_comptabilises).append(ligne)
    # Paiements en espèces saisis depuis le départ mais DATÉS d'avant : la caisse les compte le jour de la saisie, la
    # comptabilité les range avant la période. Les RÉÉMISSIONS (« …-1 », annulation d'une facture) ne sont pas du
    # nouvel argent : sur 17 paiements (3 511,301 DT au 24/11/2026), 12 en étaient — reste 5, 1 218,301 DT.
    antidates = [{"name": r.name, "date": str(r.posting_date), "saisi_le": str(getdate(r.creation)),
                  "montant": flt(r.paid_amount, 3), "client": r.party_name or "", "saisi_par": frappe.utils.get_fullname(r.owner)}
                 for r in frappe.db.sql(
                     """SELECT name, posting_date, creation, paid_amount, party_name, owner FROM `tabPayment Entry`
                        WHERE docstatus = 1 AND payment_type = 'Receive' AND paid_to = %s AND posting_date < %s
                          AND creation >= %s AND amended_from IS NULL ORDER BY creation""",
                     (COMPTE_ESPECES, depart, depart), as_dict=True)]
    return {
        "antidates_avant_depart": {"nb": len(antidates), "total": flt(sum(a["montant"] for a in antidates), 3),
                                   "liste": antidates},
        "apports_clotures": apports_clotures, "verses_banque": verses_banque,
        "depart": str(depart), "solde_avant": avant, "solde_jour": au_jour, "mouvements": mouvements,
        "entre_paiements": entre_paiements, "sorti_paiements": sorti_paiements, "depenses_caisse": dep_caisse,
        "transferts": flt(mouvements - (entre_paiements - sorti_paiements - dep_caisse), 3),
        "encaisse_clotures": encaisse, "depense_clotures": depense, "nb_clotures": len(clotures),
        "reconstitue": reconstitue, "ecart": ecart, "termes": termes,
        "reste": flt(ecart - sum(termes.values()), 3),
        "sauts": sauts, "mal_comptabilises": mal_comptabilises,
    }


def _partenaires(jour, cfg):
    """Ce qui est parti chez les partenaires (Economic Aqua Solution) : les paiements de nos clients encaissés sur LEUR
    compte, depuis la date de départ des caisses. Ce n'est pas de l'argent liquide chez nous : à part du total."""
    depart = getdate(cfg.get("date_depart") or jour)
    lignes = frappe.db.sql(
        """SELECT name, posting_date, paid_to, paid_amount, mode_of_payment, party_name, owner FROM `tabPayment Entry`
           WHERE docstatus = 1 AND payment_type = 'Receive' AND paid_to IN %s AND posting_date BETWEEN %s AND %s
           ORDER BY posting_date DESC, creation DESC""", (COMPTES_PARTENAIRE, depart, jour), as_dict=True)
    par_compte = {}
    for l in lignes:
        x = par_compte.setdefault(l.paid_to, {"compte": l.paid_to, "nom": l.paid_to.replace(" - A&S", ""), "total": 0.0,
                                              "especes": 0.0, "nb": 0, "derniers": []})
        x["total"] = flt(x["total"] + flt(l.paid_amount), 3)
        x["nb"] += 1
        if (l.mode_of_payment or "") == "Espèces":
            x["especes"] = flt(x["especes"] + flt(l.paid_amount), 3)
        if len(x["derniers"]) < 10:
            x["derniers"].append({"name": l.name, "date": str(l.posting_date), "montant": flt(l.paid_amount, 3),
                                  "mode": l.mode_of_payment or "", "client": l.party_name or ""})
    return {"depart": str(depart), "comptes": sorted(par_compte.values(), key=lambda x: -x["total"])}


def _portefeuille(jour, cfg):
    """Les chèques et traites reçus et pas encore versés : les paiements encore sur les comptes d'attente, saisis au
    plus tard le jour choisi. Le passé ne se reconstitue pas (le robot bancaire déplace le paiement vers la banque à
    l'encaissement) : pour une date passée, ce sont les pièces d'AUJOURD'HUI déjà reçues ce jour-là.
    Une pièce sur un bordereau de remise (ou marquée « remise en banque ») est à la banque, en attente du crédit."""
    from customization_app import caisse_collecte as CC
    titulaire = cfg.get("responsable")
    fin = "%s 23:59:59" % jour
    lignes = frappe.db.sql(
        """SELECT name, creation, owner, paid_to, paid_amount, party_name, party, reference_no, reference_date,
                  custom_remise_en_banque, amended_from
           FROM `tabPayment Entry` WHERE docstatus = 1 AND paid_to IN %s
           ORDER BY creation""", (tuple(COMPTES_PORTEFEUILLE),), as_dict=True)
    # Un paiement corrigé (« …-1 ») garde la date et l'auteur de sa PREMIÈRE saisie : la pièce est là depuis.
    for l in lignes:
        origine, tours = l.amended_from, 0
        while origine and tours < 10:
            o = frappe.db.get_value("Payment Entry", origine, ["creation", "owner", "amended_from"], as_dict=True)
            if not o:
                break
            l.creation, l.owner, origine, tours = o.creation, o.owner, o.amended_from, tours + 1
    lignes = sorted((l for l in lignes if get_datetime(l.creation) <= get_datetime(fin)), key=lambda l: l.creation)
    bordereaux = {}
    if lignes and frappe.db.table_exists("BRS Bordereau Remise Piece"):
        for b in frappe.db.sql(
                """SELECT p.paiement, b.name, b.date_remise, b.banque FROM `tabBRS Bordereau Remise Piece` p
                   JOIN `tabBRS Bordereau Remise` b ON b.name = p.parent
                   WHERE b.docstatus < 2 AND p.paiement IN %s""", (tuple(l.name for l in lignes),), as_dict=True):
            bordereaux[b.paiement] = b
    actifs = {e.user_id: e.employee_name for e in frappe.get_all(
        "Employee", filters={"status": "Active", "user_id": ["is", "set"]}, fields=["user_id", "employee_name"])}
    responsables = set(cfg.get("responsables") or []) | ({titulaire} if titulaire else set())
    principal = frappe.utils.get_fullname(titulaire) if titulaire else _("le collecteur principal")
    pieces = []
    for l in lignes:
        entre = getdate(l.creation)
        collectee = False
        if l.owner in actifs and l.owner not in responsables:
            collectee = bool(frappe.db.sql(
                """SELECT name FROM `tabCloture Caisse` WHERE caisse = %s AND docstatus = 1 AND date_cloture >= %s
                     AND IFNULL(collecte_le, compte_le) <= %s LIMIT 1""", (actifs[l.owner], entre, fin)))
        chez = detenteur_piece(l.owner, actifs, responsables, collectee)
        b = bordereaux.get(l.name)
        depose = (getdate(b.date_remise) if b and b.date_remise else None) or \
            (getdate(l.custom_remise_en_banque) if l.custom_remise_en_banque else None)
        if depose and depose > getdate(jour):
            depose = None
        pieces.append({
            "name": l.name, "type": COMPTES_PORTEFEUILLE[l.paid_to], "compte": l.paid_to,
            "numero": l.reference_no or "", "client": l.party_name or l.party or "", "montant": flt(l.paid_amount, 3),
            "date_piece": str(l.reference_date) if l.reference_date else None, "entre_le": str(entre),
            "saisi_par": frappe.utils.get_fullname(l.owner), "age_jours": date_diff(jour, entre),
            "chez": chez or principal, "chez_principal": chez is None,
            "depose_le": str(depose) if depose else None, "bordereau": (b.name if b else None),
        })
    en_main = [p for p in pieces if not p["depose_le"]]
    resume = {}
    for p in en_main:
        r = resume.setdefault(p["type"], {"nb": 0, "total": 0.0})
        r["nb"] += 1
        r["total"] = flt(r["total"] + p["montant"], 3)
    out = {"pieces": pieces, "par_type": resume, "nb": len(en_main),
           "total": flt(sum(p["montant"] for p in en_main), 3), "principal": principal,
           "chez_employes": [p for p in en_main if not p["chez_principal"]],
           "deposes": {"nb": len(pieces) - len(en_main), "total": flt(sum(p["montant"] for p in pieces if p["depose_le"]), 3)},
           "anciennes": [p["name"] for p in en_main if p["age_jours"] > PIECE_ANCIENNE_JOURS],
           "date_passee": getdate(jour) < getdate(nowdate()), "solde_comptable": None, "ecart_comptable": None}
    # Le contrôle : la liste doit refaire le solde des deux comptes d'attente (seulement pour aujourd'hui).
    if not out["date_passee"]:
        solde = flt(sum(_solde(compte, jour) for compte in COMPTES_PORTEFEUILLE), 3)
        out["solde_comptable"] = solde
        out["ecart_comptable"] = flt(solde - sum(p["montant"] for p in pieces), 3)
    return out


#: Les endroits SUIVIS par le graphique de flux (stocks : ce qui était là + entré − sorti = ce qui reste).
POCHES_FLUX = (("especes", "Espèces (caisses et collecteur)"), ("sabah", "Coffre « Sabah Espèces »"),
               ("pieces", "Chèques et traites en attente"), ("dettes", "Dettes clients (pas encore payé)"),
               ("partenaire", "Chez le partenaire"))


def repartir_flux(nets, source, puits):
    """Les flux d'UNE pièce comptable entre les endroits suivis. PURE.
    `nets` : {endroit: débit − crédit} (banque comprise) ; `source` / `puits` : le nom de l'extérieur si la pièce fait
    entrer (ou sortir) de l'argent. Ce qui baisse donne, ce qui monte reçoit, au prorata. -> [(de, vers, montant)]"""
    donnent = {k: -v for k, v in nets.items() if v < -0.0005}
    recoivent = {k: v for k, v in nets.items() if v > 0.0005}
    solde = sum(nets.values())
    if solde > 0.0005:
        donnent[source] = donnent.get(source, 0) + solde          # l'extérieur apporte la différence
    elif solde < -0.0005:
        recoivent[puits] = recoivent.get(puits, 0) - solde          # l'extérieur reçoit la différence
    total = sum(donnent.values())
    if total <= 0.0005:
        return []
    return [(d, r, round(md * mr / total, 3)) for d, md in donnent.items() for r, mr in recoivent.items()
            if d != r and md * mr / total > 0.0005]


def _flux(jour, cfg):
    """Le chemin de TOUT l'argent encaissé, du départ des caisses au jour choisi (demande du 25/11/2026 : « les caisses,
    l'espèce, les chèques, la banque, les dettes s'équilibrent, non ? »). Lu dans le grand livre, donc exact : pour chaque
    endroit suivi, ce qui était là + ce qui est entré − ce qui est sorti = ce qui reste. La banque est une arrivée (ce
    qu'elle paie ensuite n'est pas suivi ici)."""
    from customization_app.caisse_cloture import COMPTE_DETTES
    depart = getdate(cfg.get("date_depart") or jour)
    # « Depenses personnelle » est typé « Bank » dans le plan comptable : ce n'est pas une banque, c'est une sortie.
    banques = {c for c in frappe.get_all("Account", filters={"account_type": "Bank", "is_group": 0}, pluck="name")
               if "personnel" not in c.lower()}
    endroit = {COMPTE_ESPECES: "especes", COMPTE_SABAH: "sabah", COMPTE_DETTES: "dettes"}
    endroit.update({c: "pieces" for c in COMPTES_PORTEFEUILLE})
    endroit.update({c: "partenaire" for c in COMPTES_PARTENAIRE})
    endroit.update({c: "banque" for c in banques if c not in endroit})   # un compte suivi le reste, même typé « Bank »
    suivis = [c for c, e in endroit.items() if e != "banque"]
    lignes = frappe.db.sql(
        """SELECT g.voucher_type, g.voucher_no, g.account, SUM(g.debit - g.credit) AS net
           FROM `tabGL Entry` g
           WHERE g.is_cancelled = 0 AND g.posting_date BETWEEN %(d)s AND %(j)s
             AND g.voucher_no IN (SELECT DISTINCT voucher_no FROM `tabGL Entry`
                                  WHERE is_cancelled = 0 AND posting_date BETWEEN %(d)s AND %(j)s AND account IN %(suivis)s)
           GROUP BY g.voucher_type, g.voucher_no, g.account""",
        {"d": depart, "j": jour, "suivis": tuple(suivis)}, as_dict=True)
    pieces = {}
    for l in lignes:
        p = pieces.setdefault((l.voucher_type, l.voucher_no), {"nets": {}, "autres": []})
        e = endroit.get(l.account)
        if e:
            p["nets"][e] = p["nets"].get(e, 0) + flt(l.net)
        else:
            p["autres"].append(l.account)
    depenses_caisse = set(frappe.db.sql_list(
        """SELECT name FROM `tabJournal Entry` WHERE docstatus = 1 AND posting_date BETWEEN %s AND %s
           AND cheque_no LIKE 'Dépense caisse%%'""", (depart, jour)))
    flux = {}
    for (type_piece, nom), p in pieces.items():
        if type_piece == "Payment Entry":
            source, puits = "clients", "fournisseurs"
        else:
            source = "autres_entrees"
            puits = "perso" if any("personnel" in (a or "").lower() for a in p["autres"]) else \
                "depenses" if nom in depenses_caisse else "autres_sorties"
        for de, vers, montant in repartir_flux(p["nets"], source, puits):
            if de == "banque" and vers == "banque":
                continue
            flux[(de, vers)] = flt(flux.get((de, vers), 0) + montant, 3)
    # Les encaissements arrivés DIRECTEMENT en banque (virement, carte, espèces versées sur le compte) : ils ne touchent
    # aucun endroit suivi, on les ajoute pour que le graphique montre tout ce que les clients ont payé.
    direct = flt(frappe.db.sql(
        """SELECT SUM(paid_amount) FROM `tabPayment Entry` WHERE docstatus = 1 AND payment_type = 'Receive'
           AND paid_to IN %s AND posting_date BETWEEN %s AND %s""", (tuple(b for b in banques if endroit.get(b) == "banque") or ("",), depart, jour))[0][0], 3)
    if direct:
        flux[("clients", "banque")] = flt(flux.get(("clients", "banque"), 0) + direct, 3)
    solde = lambda comptes, cond, *v: flt(frappe.db.sql(  # noqa: E731
        "SELECT SUM(debit - credit) FROM `tabGL Entry` WHERE is_cancelled = 0 AND account IN %s AND " + cond,
        (tuple(comptes),) + v)[0][0], 3) if comptes else 0.0
    stocks = []
    for cle, libelle in POCHES_FLUX:
        comptes = [c for c, e in endroit.items() if e == cle]
        avant, apres = solde(comptes, "posting_date < %s", depart), solde(comptes, "posting_date <= %s", jour)
        entre = flt(sum(m for (de, vers), m in flux.items() if vers == cle), 3)
        sorti = flt(sum(m for (de, vers), m in flux.items() if de == cle), 3)
        stocks.append({"cle": cle, "libelle": libelle, "avant": avant, "entre": entre, "sorti": sorti, "apres": apres,
                       # 0 par construction ; autre chose = une pièce modifiée pendant le calcul.
                       "reste": flt(avant + entre - sorti - apres, 3)})
    return {"depart": str(depart), "jour": str(jour), "stocks": stocks,
            "liens": [{"de": de, "vers": vers, "montant": m} for (de, vers), m in sorted(flux.items(), key=lambda x: -x[1])]}


def _a_avoir_en_main(employes, collecteurs, portefeuille, sauts, cfg, solde_compte=None):
    """Ce que le collecteur principal doit avoir entre les mains, à recompter d'un coup : l'argent de sa propre
    caisse, l'argent collecté pas encore versé, ses chèques et traites. Le saut de chaîne de sa caisse est rappelé."""
    titulaire = cfg.get("responsable")
    x = next((c for c in collecteurs if c.get("principal")), None)
    if not titulaire or not x:
        return None
    propre = next((e for e in employes if e["caisse"] == x["caisse"]), None)
    caisse = flt(propre["total"], 3) if propre else 0.0
    pieces = [p for p in portefeuille["pieces"] if p["chez_principal"] and not p["depose_le"]]
    par_type = {}
    for p in pieces:
        r = par_type.setdefault(p["type"], {"nb": 0, "total": 0.0})
        r["nb"] += 1
        r["total"] = flt(r["total"] + p["montant"], 3)
    especes = flt(caisse + flt(x["detient"]), 3)
    # LA RÉFÉRENCE (sa remarque du 25/11/2026 : « normalement chez le collecteur, c'est ce qu'il y a en espèces ») :
    # tout l'argent liquide hors coffre est chez un employé ou chez lui — il doit donc avoir le solde du compte
    # « Espèces » moins ce que tiennent les autres. L'ancien calcul (comptages + collectes − versements) devient le
    # CONTRÔLE : leur différence est ce qui reste à régulariser. Affichage seulement, aucune écriture.
    autres = flt(sum(flt(e["total"]) for e in employes if e["caisse"] != x["caisse"]), 3)
    selon_compte = flt(flt(solde_compte) - autres, 3) if solde_compte is not None else especes
    return {"nom": x["nom"], "caisse": x["caisse"], "caisse_propre": caisse, "collecte": flt(x["detient"], 3),
            "remis_par": x.get("remis_par") or [], "especes": especes, "pieces": par_type,
            "selon_compte": selon_compte, "compte_especes": flt(solde_compte, 3) if solde_compte is not None else None,
            "autres_caisses": autres, "ecart_controle": flt(especes - selon_compte, 3),
            "total": flt(selon_compte + sum(r["total"] for r in par_type.values()), 3),
            "sauts": [s for s in sauts if s["caisse"] == x["caisse"]]}


@frappe.whitelist()
def situation(date=None):
    from customization_app import caisse_collecte as CC
    from customization_app import caisse_regularisations as REG
    if not CC.peut_voir_toutes_les_caisses():
        frappe.throw(_("Réservé aux responsables de collecte et à la direction."), frappe.PermissionError)
    jour = min(getdate(date or nowdate()), getdate(nowdate()))
    cfg = CC.config()
    regs = REG.regularisations(jour)
    effets = REG.effets(regs)
    depart = getdate(cfg.get("date_depart") or jour)
    apports_liste = apports(depart, jour)
    employes = _employes(jour, cfg, apports_liste, effets)
    collecteurs, plafond, jours_max = _collecteurs(jour, cfg, {a["name"] for a in apports_liste}, effets)
    sabah = _solde(COMPTE_SABAH, jour)
    versements = [{"name": v.name, "date": str(v.posting_date), "montant": flt(v.montant, 3),
                   "depuis": (v.depuis or "").replace(" - A&S", ""), "banque": (v.banque or "").replace(" - A&S", ""),
                   "reference": reference(v.user_remark, v.cheque_no), "saisie_par": frappe.utils.get_fullname(v.owner)}
                  for v in _versements(jour)]
    chez_employes = flt(sum(e["total"] for e in employes), 3)
    chez_collecteurs = flt(sum(max(x["detient"], 0) for x in collecteurs), 3)
    rapprochement = _rapprochement(jour, cfg, employes, collecteurs, effets)
    portefeuille = _portefeuille(jour, cfg)
    principal = _a_avoir_en_main(employes, collecteurs, portefeuille, rapprochement["sauts"], cfg,
                                 _solde(COMPTE_ESPECES, jour))
    # LES ANOMALIES (25/11/2026 : « les erreurs identifiées, ne pas les montrer, les corriger ») : chacune a sa clé ;
    # régularisée par la direction ou absorbée par un inventaire du collecteur, elle quitte la carte pour le journal.
    anomalies = REG.construire_anomalies(
        rapprochement["sauts"], [m for x in collecteurs for m in x.get("entrees_manuelles") or []],
        rapprochement["antidates_avant_depart"]["liste"], rapprochement["mal_comptabilises"],
        [m for x in collecteurs for m in x.get("entrees_detail") or []], principal["caisse"] if principal else None)
    a_regulariser, traitees = REG.trier(anomalies, regs, principal["caisse"] if principal else None)
    if principal:
        # Le plafond se juge sur ce qu'il DOIT avoir d'après le compte, pas sur le calcul de contrôle.
        for x in collecteurs:
            if x.get("principal"):
                x["alertes"] = alertes_collecteur(principal["selon_compte"], plafond, jours_max, x.get("depuis"), x.get("age_jours") or 0)
        principal["sauts"] = [x for x in principal["sauts"] if ("saut:" + x["cloture"]) in {a["cle"] for a in a_regulariser}]
        principal["inventaire"] = REG.dernier_inventaire(regs, principal["caisse"], "Collecte")
    return {
        "date": str(jour), "date_depart": cfg.get("date_depart"), "plafond": plafond, "jours_max": jours_max,
        "totaux": {"employes": chez_employes, "collecteurs": chez_collecteurs, "sabah": sabah,
                   "total": flt(chez_employes + chez_collecteurs + sabah, 3),
                   "comptable_especes": _solde(COMPTE_ESPECES, jour), "pieces": portefeuille["total"]},
        "employes": employes, "collecteurs": collecteurs, "versements": versements,
        "jours": _jour_par_jour(jour),
        "rapprochement": rapprochement,
        "partenaires": _partenaires(jour, cfg),
        "portefeuille": portefeuille,
        "principal": principal,
        "apports": apports_liste, "apports_depuis": str(apports_depuis() or ""),
        "a_regulariser": a_regulariser, "anomalies_traitees": traitees,
        "regularisations": REG.journal(regs),
        "inventaire_coffre": REG.dernier_inventaire(regs, REG.COFFRE, "Coffre"),
        "peut_regulariser": REG.peut_regulariser(), "peut_inventorier": REG.peut_inventorier(cfg),
        "flux": _flux(jour, cfg),
    }
