"""« Où est l'argent liquide » (demande du 24/11/2026) — page /app/argent-liquide.

Toutes les caisses partagent UN compte comptable (« Espèces - A&S ») : la comptabilité ne dit pas qui tient les
billets, et son solde ne recoupe pas les caisses (remises à zéro le 05/10/2026). La page reconstruit donc, à une date,
où se trouve l'argent à partir des FLUX enregistrés depuis la date de départ des caisses (Config Caisse) :

  - chez chaque EMPLOYÉ : le fond gardé à sa dernière clôture, ce qu'il a compté mais pas encore remis (clôture en
    attente de collecte) et ce qui n'est pas encore compté (encaissé − dépensé en espèces depuis sa dernière clôture) ;
  - chez chaque COLLECTEUR : ce qu'il a reçu aux collectes (+ passations reçues, − passations remises), moins ce
    qu'il a sorti du compte Espèces par écriture (vers Sabah Espèces, la banque, des dépenses personnelles…) —
    une écriture saisie par quelqu'un qui n'est pas collecteur est imputée au responsable titulaire ;
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

COMPTE_ESPECES = "Espèces - A&S"
COMPTE_SABAH = "Sabah Espèces - A&S"
# Les mêmes que le Server Script « get caisse situation api » (BANQUE_ACCOUNTS, patch caisse_especes_biat).
COMPTES_BANQUE = ("STE430127B - Zitouna - A&S", "BIAT - A&S")
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


def reference(remarque, cheque_no=None) -> str:
    """« Ref : TT263168Q77X » ou « réf. TT26306750847374 » dans la remarque d'un versement. Fonction pure."""
    m = re.search(r"r[ée]f\.?\s*:?\s*([A-Z0-9][A-Z0-9-]{5,})", remarque or "", re.I)
    return m.group(1) if m else (cheque_no or "")


def _employes(jour, cfg):
    from customization_app import caisse_collecte as CC
    from customization_app import caisse_cloture as CL
    from customization_app.rapport_caisse_journaliere import get_data
    # Employés partis : leur caisse n'encaisse plus, on ne recalcule pas un mois de « non compté » pour rien.
    partis = set(frappe.get_all("Employee", filters={"status": ["!=", "Active"]}, pluck="employee_name"))
    caisses = []
    for caisse in CC.noms_caisses():
        if caisse == CL.CAISSE_GLOBALE:
            continue
        depart = getdate(CC.date_depart_pour(caisse, cfg) or cfg.get("date_depart") or jour)
        derniere = frappe.db.sql(
            """SELECT name, date_cloture, statut, docstatus, especes_remises, fond_conserve
               FROM `tabCloture Caisse` WHERE caisse = %s AND date_cloture BETWEEN %s AND %s AND docstatus < 2
               ORDER BY date_cloture DESC, creation DESC LIMIT 1""", (caisse, depart, jour), as_dict=True)
        derniere = derniere[0] if derniere else None
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
            non_comptes[caisse] = (flt(especes - depenses, 3), (date_diff(jour, debut) + 1) if (especes or depenses) else 0)
    lignes = []
    for caisse, derniere, _d in caisses:
        fond = flt(derniere.fond_conserve, 3) if derniere else 0.0
        a_remettre = flt(derniere.especes_remises, 3) if derniere and derniere.docstatus == 0 \
            and derniere.statut in CC.STATUTS_EN_ATTENTE else 0.0
        non_compte, jours_non_comptes = non_comptes.get(caisse, (0.0, 0))
        total = flt(fond + a_remettre + non_compte, 3)
        if not derniere and not total:
            continue                      # caisse jamais utilisée (assistants…)
        lignes.append({"caisse": caisse, "fond": fond, "a_remettre": a_remettre, "non_compte": non_compte,
                       "jours_non_comptes": jours_non_comptes, "total": total,
                       "derniere": ({"name": derniere.name, "date": str(derniere.date_cloture), "statut": derniere.statut}
                                    if derniere else None)})
    return lignes


def _collecteurs(jour, cfg):
    """Ce que chaque collecteur détient : reçu aux collectes ± passations − sorties du compte Espèces."""
    from customization_app import caisse_collecte as CC
    depart = getdate(cfg.get("date_depart") or jour)
    fin = "%s 23:59:59" % jour
    titulaire = cfg.get("responsable")
    collecteurs = {}

    def c(user):
        return collecteurs.setdefault(user, {"user": user, "nom": frappe.utils.get_fullname(user) or user,
                                             "caisse": CC._caisse_de(user), "recu": 0.0, "passations": 0.0,
                                             "sorties": {}, "entrees_autres": 0.0, "detail_sorties": [], "_entrees": []})

    for u in (cfg.get("responsables") or []) + (cfg.get("delegues") or []):
        c(u)
    for r in frappe.db.sql(
            """SELECT collecte_par, collecte_le, especes_recues FROM `tabCloture Caisse`
               WHERE docstatus = 1 AND IFNULL(collecte_par, '') != '' AND date_cloture >= %s AND collecte_le <= %s
                 AND IFNULL(passation, '') = '' ORDER BY collecte_le""", (depart, fin), as_dict=True):
        if flt(r.especes_recues):
            x = c(r.collecte_par)
            x["recu"] += flt(r.especes_recues)
            x["_entrees"].append((getdate(r.collecte_le), flt(r.especes_recues)))
    # Passations : le délégué a collecté (ses clôtures portent la passation), le responsable reçoit à la validation.
    for r in frappe.db.sql(
            """SELECT delegue, responsable, validee_le, especes_recues FROM `tabPassation Caisse`
               WHERE docstatus = 1 AND validee_le BETWEEN %s AND %s""", ("%s 00:00:00" % depart, fin), as_dict=True):
        if r.responsable and flt(r.especes_recues):
            x = c(r.responsable)
            x["passations"] += flt(r.especes_recues)
            x["_entrees"].append((getdate(r.validee_le), flt(r.especes_recues)))
    # Sorties (et retours) du compte Espèces qui ne sont pas des dépenses de caisse.
    for r in frappe.db.sql(
            """SELECT je.name, je.posting_date, je.owner, je.cheque_no, je.user_remark,
                      SUM(CASE WHEN jea.account = %(esp)s THEN jea.credit_in_account_currency - jea.debit_in_account_currency ELSE 0 END) AS sortie,
                      GROUP_CONCAT(DISTINCT CASE WHEN jea.account != %(esp)s THEN jea.account END) AS contre
               FROM `tabJournal Entry` je JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
               WHERE je.docstatus = 1 AND je.posting_date BETWEEN %(d)s AND %(j)s
                 AND IFNULL(je.cheque_no, '') NOT LIKE 'Dépense caisse%%'
               GROUP BY je.name HAVING SUM(CASE WHEN jea.account = %(esp)s THEN 1 ELSE 0 END) > 0
               ORDER BY je.posting_date, je.creation""",
            {"esp": COMPTE_ESPECES, "d": depart, "j": jour}, as_dict=True):
        qui = r.owner if r.owner in collecteurs else titulaire
        if not qui:
            continue
        x = c(qui)
        cat = categorie((r.contre or "").split(",")[0])
        if flt(r.sortie) >= 0:
            x["sorties"][cat] = flt(x["sorties"].get(cat)) + flt(r.sortie)
        else:
            x["entrees_autres"] += -flt(r.sortie)
            x["_entrees"].append((getdate(r.posting_date), -flt(r.sortie)))
        x["detail_sorties"].append({"name": r.name, "date": str(r.posting_date), "montant": flt(r.sortie, 3),
                                    "vers": r.contre or "", "categorie": cat, "saisie_par": frappe.utils.get_fullname(r.owner),
                                    "reference": reference(r.user_remark, r.cheque_no)})
    plafond = _reglage("plafond_collecteur", PLAFOND_DEFAUT)
    jours_max = int(_reglage("jours_plafond_collecteur", JOURS_DEFAUT))
    out = []
    for x in collecteurs.values():
        sorties = flt(sum(x["sorties"].values()), 3)
        x["sorties_total"] = sorties
        x["detient"] = flt(x["recu"] + x["passations"] + x["entrees_autres"] - sorties, 3)
        depuis, age = anciennete(sorted(x.pop("_entrees")), sorties, jour)
        x["depuis"], x["age_jours"] = (str(depuis) if depuis else None), age
        alertes = []
        if plafond and x["detient"] > plafond + 0.0005:
            alertes.append(_("garde {0} DT, au-delà du plafond de {1} DT").format(_fmt(x["detient"]), _fmt(plafond)))
        if jours_max and x["detient"] > 0.5 and age > jours_max:
            alertes.append(_("de l'argent collecté le {0} n'est toujours pas versé ({1} jours)").format(
                getdate(depuis).strftime("%d/%m/%Y"), age))
        x["alertes"] = alertes
        x["detail_sorties"] = x["detail_sorties"][-15:][::-1]
        if x["recu"] or x["passations"] or sorties or x["entrees_autres"]:
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


@frappe.whitelist()
def situation(date=None):
    from customization_app import caisse_collecte as CC
    if not CC.peut_voir_toutes_les_caisses():
        frappe.throw(_("Réservé aux responsables de collecte et à la direction."), frappe.PermissionError)
    jour = min(getdate(date or nowdate()), getdate(nowdate()))
    cfg = CC.config()
    employes = _employes(jour, cfg)
    collecteurs, plafond, jours_max = _collecteurs(jour, cfg)
    sabah = _solde(COMPTE_SABAH, jour)
    versements = [{"name": v.name, "date": str(v.posting_date), "montant": flt(v.montant, 3),
                   "depuis": (v.depuis or "").replace(" - A&S", ""), "banque": (v.banque or "").replace(" - A&S", ""),
                   "reference": reference(v.user_remark, v.cheque_no), "saisie_par": frappe.utils.get_fullname(v.owner)}
                  for v in _versements(jour)]
    chez_employes = flt(sum(e["total"] for e in employes), 3)
    chez_collecteurs = flt(sum(max(x["detient"], 0) for x in collecteurs), 3)
    return {
        "date": str(jour), "date_depart": cfg.get("date_depart"), "plafond": plafond, "jours_max": jours_max,
        "totaux": {"employes": chez_employes, "collecteurs": chez_collecteurs, "sabah": sabah,
                   "total": flt(chez_employes + chez_collecteurs + sabah, 3),
                   "comptable_especes": _solde(COMPTE_ESPECES, jour)},
        "employes": employes, "collecteurs": collecteurs, "versements": versements,
        "jours": _jour_par_jour(jour),
    }
