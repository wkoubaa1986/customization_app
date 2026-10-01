"""
Congés & récupérations (Page Desk « conges-recuperations », onglet HR).

Un seul écran pour : voir les absences de chaque employé sur un planning mensuel,
poser une absence (demande de congé HRMS créée directement APPROUVÉE), attribuer
les congés annuels (Leave Allocation), et gérer les jours de récupération acquis
par quinzaine.

Tout s'appuie sur HRMS, rien n'est stocké à côté :
- une absence = `Leave Application` soumise, statut Approved, approbateur = qui
  la pose ; HRMS contrôle solde, chevauchement, jours fériés ;
- un droit = `Leave Allocation` soumise ; s'il en existe déjà une sur la période
  pour ce type, on l'AUGMENTE (HRMS refuse deux allocations qui se chevauchent) ;
- la récupération = type de congé TYPE_RECUP (créé au besoin). C'est le
  responsable qui l'AFFECTE, à un employé donné, pas à tous : soit à la main
  depuis le planning (le solde est crédité d'office s'il manque), soit par une
  Regle Recuperation « un jour toutes les N semaines à partir du … » que
  `planifier_recuperations` transforme en vrais jours (demande approuvée +
  crédit), jusqu'à aujourd'hui + horizon ; `Recuperation Acquise` trace chaque
  jour : jamais posé deux fois. Tâche quotidienne + bouton « Planifier ».
- calendrier : chaque jour d'absence approuvé devient une Tâche de travail sur
  TOUTE LA JOURNÉE pour l'employé (type « Jour de récupération » ou « Congé »),
  supprimée si l'absence est annulée (hooks Leave Application on_submit /
  on_cancel, qui remplacent le Server Script « Generer » sur Attendance).
- soldes : lus dans `Leave Ledger Entry` (la vérité d'HRMS).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_last_day, getdate, nowdate

TYPE_RECUP = "Récupération (quinzaine)"
TYPE_TACHE_RECUP = "Jour de récupération"
TYPE_TACHE_CONGE = "Congé"
MARQUE_TACHE = "Généré depuis Congés & récupérations"
ROLE = "Congés RH"   # réservé : koubaawassim + aquaworld.servicing (Nejib). Attribué par patch, jamais ici.
ROLES = (ROLE,)
PRECISION = 2
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
JOURS = ["Lu", "Ma", "Me", "Je", "Ve", "Sa", "Di"]


# --------------------------------------------------------------------------- règles pures

def dates_planifiees(date_premiere, periodicite_semaines, jusqu_a, apres=None) -> list:
    """Jours de récupération d'une règle : `date_premiere`, puis toutes les N semaines,
    jusqu'à `jusqu_a` inclus, strictement après `apres` (dernier jour déjà planifié)."""
    pas = max(cint(periodicite_semaines), 1) * 7
    d = getdate(date_premiere)
    jusqu_a = getdate(jusqu_a)
    apres = getdate(apres) if apres else None
    out = []
    while d <= jusqu_a:
        if not apres or d > apres:
            out.append(d)
        d = add_days(d, pas)
    return out


def type_tache_pour(leave_type: str) -> str:
    return TYPE_TACHE_RECUP if leave_type == TYPE_RECUP else TYPE_TACHE_CONGE


def lettre_type(leave_type: str) -> str:
    """Lettre affichée dans le planning."""
    t = (leave_type or "").lower()
    if "récup" in t or "recup" in t:
        return "R"
    if "maladie" in t:
        return "M"
    if "sans solde" in t:
        return "S"
    if "report" in t:
        return "V′"
    if "vacance" in t or "congé" in t:
        return "V"
    return (leave_type or "?")[:1].upper()


def jours_du_mois(annee: int, mois: int) -> list[dict]:
    d = getdate(f"{annee}-{mois:02d}-01")
    fin = get_last_day(d)
    out = []
    while d <= fin:
        out.append({"date": str(d), "jour": d.day, "semaine": JOURS[d.weekday()], "weekend": d.weekday() >= 5})
        d = add_days(d, 1)
    return out


# --------------------------------------------------------------------------- accès données

def _verifier_acces():
    if frappe.session.user == "Administrator":
        return
    if not set(frappe.get_roles()).intersection(ROLES):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)


def _employes() -> list[dict]:
    return frappe.get_all("Employee", filters={"status": "Active"},
                          fields=["name", "employee_name", "holiday_list", "company", "image"], order_by="employee_name")


def _feries(employee: str, debut, fin) -> dict[str, dict]:
    from hrms.hr.utils import get_holidays_for_employee
    try:
        rows = get_holidays_for_employee(employee, debut, fin, raise_exception=False)
    except Exception:
        rows = []
    return {str(r.holiday_date): {"description": r.description, "weekly_off": cint(r.get("weekly_off"))} for r in rows}


def _absences(debut, fin, employee=None) -> list[dict]:
    filtres = {"docstatus": 1, "status": "Approved", "from_date": ["<=", fin], "to_date": [">=", debut]}
    if employee:
        filtres["employee"] = employee
    return frappe.get_all("Leave Application", filters=filtres, order_by="from_date",
                          fields=["name", "employee", "employee_name", "leave_type", "from_date", "to_date",
                                  "half_day", "half_day_date", "total_leave_days", "description"])


def _types_conges() -> list[str]:
    types = frappe.get_all("Leave Type", pluck="name", order_by="name")
    if TYPE_RECUP not in types:
        types.append(TYPE_RECUP)
    return types


def _soldes(annee: int) -> dict[str, dict[str, dict]]:
    """{employé: {type: {acquis, pris, expire, reste}}} depuis le grand livre des congés."""
    rows = frappe.db.sql("""
        SELECT employee, leave_type, transaction_type, is_expired, SUM(leaves) AS jours
        FROM `tabLeave Ledger Entry`
        WHERE docstatus = 1 AND YEAR(from_date) = %s
        GROUP BY employee, leave_type, transaction_type, is_expired
    """, (annee,), as_dict=True)
    out = defaultdict(lambda: defaultdict(lambda: {"acquis": 0.0, "pris": 0.0, "expire": 0.0, "reste": 0.0}))
    for r in rows:
        s = out[r.employee][r.leave_type]
        if r.transaction_type == "Leave Allocation":
            if cint(r.is_expired):
                s["expire"] += -flt(r.jours)
            else:
                s["acquis"] += flt(r.jours)
        else:
            s["pris"] += -flt(r.jours)
    for emp in out.values():
        for s in emp.values():
            s["reste"] = flt(s["acquis"] - s["pris"] - s["expire"], PRECISION)
            for k in ("acquis", "pris", "expire"):
                s[k] = flt(s[k], PRECISION)
    return {e: dict(t) for e, t in out.items()}


@frappe.whitelist()
def get_context(annee=None, mois=None, employee=None):
    """Tout l'écran : planning du mois, soldes de l'année, règles de récupération, absences de l'année."""
    _verifier_acces()
    auj = getdate(nowdate())
    annee = cint(annee) or auj.year
    mois = cint(mois) or auj.month
    debut, fin = f"{annee}-{mois:02d}-01", str(get_last_day(getdate(f"{annee}-{mois:02d}-01")))
    employes = _employes()
    absences_mois = _absences(debut, fin)
    planning = []
    for e in employes:
        feries = _feries(e.name, debut, fin)
        cases = {}
        for a in absences_mois:
            if a.employee != e.name:
                continue
            d = max(getdate(a.from_date), getdate(debut))
            fin_a = min(getdate(a.to_date), getdate(fin))
            while d <= fin_a:
                sd = str(d)
                if sd not in feries:
                    cases[sd] = {"name": a.name, "type": a.leave_type, "lettre": lettre_type(a.leave_type),
                                 "demi": bool(cint(a.half_day) and str(a.half_day_date) == sd)}
                d = add_days(d, 1)
        planning.append({"employee": e.name, "employee_name": e.employee_name, "image": e.image or "",
                         "feries": feries, "cases": cases})
    regles = {r.employee: r for r in frappe.get_all(
        "Regle Recuperation", fields=["employee", "date_premiere", "periodicite_semaines", "horizon_semaines", "actif", "derniere_planifiee"])}
    acquis = {r.employee: flt(r.total) for r in frappe.db.sql("""
        SELECT employee, SUM(jours) AS total FROM `tabRecuperation Acquise` WHERE YEAR(date_fin) = %s GROUP BY employee
    """, (annee,), as_dict=True)}
    return {
        "annee": annee, "mois": mois, "libelle_mois": f"{MOIS[mois - 1]} {annee}", "aujourdhui": str(auj),
        "jours": jours_du_mois(annee, mois),
        "employes": [{"employee": e.name, "employee_name": e.employee_name, "image": e.image or ""} for e in employes],
        "types": _types_conges(), "type_recup": TYPE_RECUP,
        "planning": planning,
        "soldes": _soldes(annee),
        "regles": [{"employee": e.name, "employee_name": e.employee_name, "existe": e.name in regles,
                    "date_premiere": str(regles[e.name].date_premiere) if e.name in regles and regles[e.name].date_premiere else None,
                    "periodicite_semaines": cint(regles[e.name].periodicite_semaines) if e.name in regles else 2,
                    "horizon_semaines": cint(regles[e.name].horizon_semaines) if e.name in regles else 8,
                    "actif": cint(regles[e.name].actif) if e.name in regles else 0,
                    "derniere": str(regles[e.name].derniere_planifiee) if e.name in regles and regles[e.name].derniere_planifiee else None,
                    "jours_annee": acquis.get(e.name, 0.0)} for e in employes],
        "absences_annee": _absences(f"{annee}-01-01", f"{annee}-12-31", employee) if employee else [],
        "allocations_annee": frappe.get_all("Leave Allocation", filters={"docstatus": 1, "employee": employee, "to_date": [">=", f"{annee}-01-01"], "from_date": ["<=", f"{annee}-12-31"]},
                                            fields=["name", "leave_type", "from_date", "to_date", "new_leaves_allocated", "unused_leaves", "total_leaves_allocated"],
                                            order_by="from_date") if employee else [],
        "peut_modifier": frappe.has_permission("Leave Application", "create"),
    }


# --------------------------------------------------------------------------- absences

@frappe.whitelist()
def affecter_absence(employee, leave_type, from_date, to_date=None, half_day=0, half_day_date=None, motif=None):
    """Pose une absence : demande de congé HRMS créée et soumise, déjà approuvée."""
    _verifier_acces()
    to_date = to_date or from_date
    if getdate(to_date) < getdate(from_date):
        frappe.throw(_("La date de fin précède la date de début."))
    return _poser_absence(employee, leave_type, from_date, to_date, half_day, half_day_date, motif, origine="Affecté à la main")


def _poser_absence(employee, leave_type, from_date, to_date, half_day=0, half_day_date=None, motif=None, origine="Affecté à la main"):
    if leave_type == TYPE_RECUP:
        _assurer_type_recup()
        # C'est le responsable qui accorde la récupération : s'il manque du solde, on le crédite.
        from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on, get_number_of_leave_days
        besoin = flt(get_number_of_leave_days(employee, leave_type, from_date, to_date, cint(half_day), half_day_date))
        solde = flt(get_leave_balance_on(employee, leave_type, from_date, to_date, consider_all_leaves_in_the_allocation_period=True))
        if besoin > solde:
            _allocation_recup(employee, getdate(from_date).year, besoin - solde)
    emp = frappe.db.get_value("Employee", employee, ["company", "employee_name"], as_dict=True)
    doc = frappe.new_doc("Leave Application")
    doc.update({
        "employee": employee, "company": emp.company, "leave_type": leave_type,
        "from_date": from_date, "to_date": to_date,
        "half_day": cint(half_day), "half_day_date": half_day_date if cint(half_day) else None,
        "description": (motif or "").strip() or _("Posé depuis Congés & récupérations"),
        "posting_date": nowdate(), "status": "Approved", "leave_approver": frappe.session.user,
    })
    doc.insert()
    doc.submit()
    if leave_type == TYPE_RECUP:
        frappe.get_doc({"doctype": "Recuperation Acquise", "employee": employee, "date_debut": from_date, "date_fin": to_date,
                        "jours": flt(doc.total_leave_days), "origine": origine, "leave_application": doc.name}).insert()
    return {"name": doc.name, "jours": flt(doc.total_leave_days)}


@frappe.whitelist()
def annuler_absence(name):
    _verifier_acces()
    doc = frappe.get_doc("Leave Application", name)
    if doc.docstatus == 1:
        doc.cancel()
    elif doc.docstatus == 0:
        doc.delete()
    return True


# --------------------------------------------------------------------------- droits (allocations)

def _allocation_existante(employee: str, leave_type: str, from_date, to_date):
    return frappe.db.get_value("Leave Allocation", {
        "employee": employee, "leave_type": leave_type, "docstatus": 1,
        "from_date": ["<=", to_date], "to_date": [">=", from_date]}, "name")


def _augmenter_allocation(name: str, jours: float) -> str:
    """Ajoute `jours` à une allocation soumise (HRMS ajuste le grand livre)."""
    doc = frappe.get_doc("Leave Allocation", name)
    doc.new_leaves_allocated = flt(doc.new_leaves_allocated) + flt(jours)
    doc.flags.ignore_validate_update_after_submit = False
    doc.save()
    return doc.name


def _creer_allocation(employee: str, leave_type: str, from_date, to_date, jours: float, carry_forward=0) -> str:
    doc = frappe.new_doc("Leave Allocation")
    doc.update({"employee": employee, "leave_type": leave_type, "from_date": from_date, "to_date": to_date,
                "new_leaves_allocated": flt(jours), "carry_forward": cint(carry_forward)})
    doc.insert()
    doc.submit()
    return doc.name


@frappe.whitelist()
def attribuer(employees, leave_type, jours, from_date=None, to_date=None, carry_forward=0, completer=1):
    """Attribue `jours` de `leave_type` à un ou plusieurs employés sur la période (année par défaut).
    Une allocation existante sur la période est augmentée si `completer`, sinon refusée."""
    _verifier_acces()
    if isinstance(employees, str):
        try:
            employees = json.loads(employees)
        except Exception:
            employees = [employees]
    if not employees:
        frappe.throw(_("Aucun employé."))
    if flt(jours) <= 0:
        frappe.throw(_("Le nombre de jours doit être positif."))
    annee = getdate(nowdate()).year
    from_date = from_date or f"{annee}-01-01"
    to_date = to_date or f"{annee}-12-31"
    if leave_type == TYPE_RECUP:
        _assurer_type_recup()
    resultats = []
    for emp in employees:
        existante = _allocation_existante(emp, leave_type, from_date, to_date)
        if existante:
            if not cint(completer):
                frappe.throw(_("{0} a déjà l’allocation {1} sur cette période pour {2}.")
                             .format(frappe.db.get_value("Employee", emp, "employee_name"), existante, leave_type))
            resultats.append({"employee": emp, "name": _augmenter_allocation(existante, jours), "action": "augmentée"})
        else:
            resultats.append({"employee": emp, "name": _creer_allocation(emp, leave_type, from_date, to_date, jours, carry_forward),
                              "action": "créée"})
    return resultats


@frappe.whitelist()
def supprimer_allocation(name):
    """Annule une allocation (HRMS refuse si des congés ont été pris dessus)."""
    _verifier_acces()
    doc = frappe.get_doc("Leave Allocation", name)
    doc.cancel()
    return True


# --------------------------------------------------------------------------- récupération par quinzaine

def _assurer_type_recup() -> str:
    """Type de congé « Récupération (quinzaine) » : allouable, reportable, sans expiration."""
    if frappe.db.exists("Leave Type", TYPE_RECUP):
        return TYPE_RECUP
    frappe.get_doc({"doctype": "Leave Type", "leave_type_name": TYPE_RECUP, "is_carry_forward": 1,
                    "include_holiday": 0, "is_compensatory": 0, "is_lwp": 0, "allow_negative": 0,
                    "max_leaves_allowed": 0}).insert(ignore_permissions=True)
    return TYPE_RECUP


@frappe.whitelist()
def enregistrer_regle(employee, date_premiere, periodicite_semaines=2, horizon_semaines=8, actif=1, planifier=1):
    """Enregistre la règle et, si elle est active, pose tout de suite ses jours jusqu'à l'horizon :
    l'utilisateur voit le résultat sans second clic."""
    _verifier_acces()
    if frappe.db.exists("Regle Recuperation", employee):
        doc = frappe.get_doc("Regle Recuperation", employee)
        if str(doc.date_premiere) != str(date_premiere) or cint(doc.periodicite_semaines) != cint(periodicite_semaines):
            doc.derniere_planifiee = None   # nouveau rythme : on repart du premier jour (les jours déjà tracés sont sautés)
    else:
        doc = frappe.new_doc("Regle Recuperation")
        doc.employee = employee
    doc.update({"date_premiere": date_premiere, "periodicite_semaines": cint(periodicite_semaines) or 2,
                "horizon_semaines": cint(horizon_semaines) or 8, "actif": cint(actif)})
    doc.save()
    resultat = {"name": doc.name, "poses": [], "sautes": []}
    if cint(actif) and cint(planifier):
        _assurer_type_recup()
        resultat.update(_planifier_regle(frappe.get_doc("Regle Recuperation", doc.name)))
    return resultat


@frappe.whitelist()
def supprimer_regle(employee):
    _verifier_acces()
    if frappe.db.exists("Regle Recuperation", employee):
        frappe.delete_doc("Regle Recuperation", employee)
    return True


def _allocation_recup(employee: str, annee: int, jours: float) -> str:
    """Allocation de récupération de l'année : augmentée si elle existe, sinon créée avec report
    des jours non pris de l'année précédente (pas d'expiration)."""
    debut, fin = f"{annee}-01-01", f"{annee}-12-31"
    existante = _allocation_existante(employee, TYPE_RECUP, debut, fin)
    if existante:
        return _augmenter_allocation(existante, jours)
    precedente = frappe.db.exists("Leave Allocation", {"employee": employee, "leave_type": TYPE_RECUP, "docstatus": 1,
                                                       "to_date": ["<", debut]})
    return _creer_allocation(employee, TYPE_RECUP, debut, fin, jours, carry_forward=1 if precedente else 0)


@frappe.whitelist()
def planifier_recuperations(jusqu_a=None, employee=None):
    """Pose les jours de récupération de chaque règle active (ou de celle d'`employee`) jusqu'à
    aujourd'hui + horizon (ou `jusqu_a`) : un jour = demande approuvée + crédit + tâche calendrier (hook).
    Idempotent : un jour déjà tracé (Recuperation Acquise) ou férié est sauté."""
    _verifier_acces()
    _assurer_type_recup()
    resume = []
    filtres = {"actif": 1}
    if employee:
        filtres["employee"] = employee
    for r in frappe.get_all("Regle Recuperation", filters=filtres,
                            fields=["name", "employee", "employee_name", "date_premiere", "periodicite_semaines",
                                    "horizon_semaines", "derniere_planifiee"]):
        res = _planifier_regle(r, jusqu_a)
        if res["poses"] or res["sautes"]:
            resume.append({"employee": r.employee, "employee_name": r.employee_name, **res})
    return resume


def _planifier_regle(r, jusqu_a=None) -> dict:
    """Pose les jours d'UNE règle jusqu'à `jusqu_a` (défaut : aujourd'hui + horizon)."""
    if not r.date_premiere:
        return {"poses": [], "sautes": []}
    horizon = getdate(jusqu_a) if jusqu_a else add_days(getdate(nowdate()), 7 * (cint(r.horizon_semaines) or 8))
    poses, sautes = [], []
    derniere = r.derniere_planifiee
    for d in dates_planifiees(r.date_premiere, r.periodicite_semaines, horizon, r.derniere_planifiee):
        sd = str(d)
        derniere = d
        if frappe.db.exists("Recuperation Acquise", {"employee": r.employee, "date_debut": sd, "date_fin": sd}):
            continue
        if sd in _feries(r.employee, sd, sd):
            sautes.append(sd)
            continue
        try:
            _poser_absence(r.employee, TYPE_RECUP, sd, sd, motif=_("Récupération planifiée (règle)"), origine="Planifié")
            poses.append(sd)
        except Exception as e:
            # Présence pointée ce jour-là, chevauchement… : on note et on continue.
            sautes.append(f"{sd} ({frappe.utils.cstr(e)[:80]})")
    if derniere != r.derniere_planifiee:
        frappe.db.set_value("Regle Recuperation", r.name, "derniere_planifiee", derniere)
    return {"poses": poses, "sautes": sautes}


# --------------------------------------------------------------------------- calendrier de travail

def _tache_manuelle_du_jour(employee: str, date: str, leave_type: str):
    """Tâche déjà posée à la main ce jour-là pour la même raison (ex. « Jour de récuperation
    bi-hebdomadaire » créées avant la page) : on la reprend plutôt que d'en ajouter une seconde."""
    motif = "%récup%" if leave_type == TYPE_RECUP else "%congé%"
    noms = frappe.get_all("Tache de travail", filters={
        "custom_choix_du_staff": employee, "status": ["!=", "Cancelled"],
        "starts_on": ["between", [f"{date} 00:00:00", f"{date} 23:59:59"]],
        "custom_type_dintervention": ["in", ["Autre", TYPE_TACHE_RECUP, TYPE_TACHE_CONGE]],
        "subject": ["not like", f"%{MARQUE_TACHE}%"],
    }, or_filters={"subject": ["like", motif], "titre": ["like", motif]}, pluck="name", limit=1)
    return noms[0] if noms else None


def _tache_absence(leave, date: str) -> str:
    """Tâche de travail sur toute la journée pour un jour d'absence approuvé."""
    from customization_app.api import compute_tache_color
    emp_name = leave.employee_name or frappe.db.get_value("Employee", leave.employee, "employee_name")
    existante = _tache_manuelle_du_jour(leave.employee, date, leave.leave_type)
    t = frappe.get_doc("Tache de travail", existante) if existante else frappe.new_doc("Tache de travail")
    t.update({
        "custom_type_dintervention": type_tache_pour(leave.leave_type),
        "custom_choix_du_staff": leave.employee, "custom_employé": emp_name,
        "starts_on": f"{date} 08:00:00", "ends_on": f"{date} 18:00:00", "toute_la_journée": 1,
        "titre": f"{leave.leave_type}\n{emp_name}",
        "subject": f"{leave.leave_type}\n{emp_name}\n{MARQUE_TACHE} ({leave.name})",
        "status": "Open",
    })
    t.color = compute_tache_color(t)
    t.flags.ignore_permissions = True
    t.save() if existante else t.insert()
    return t.name


def leave_application_on_submit(doc, method=None):
    """Hook : une tâche calendrier « toute la journée » par jour de présence « On Leave » créé par HRMS."""
    if doc.status != "Approved":
        return
    for a in frappe.get_all("Attendance", filters={"leave_application": doc.name, "docstatus": 1,
                                                    "status": ["in", ["On Leave", "Half Day"]]},
                            fields=["name", "attendance_date", "custom_lien_in_calendrier"]):
        if a.custom_lien_in_calendrier and frappe.db.exists("Tache de travail", a.custom_lien_in_calendrier):
            continue
        nom = _tache_absence(doc, str(a.attendance_date))
        frappe.db.set_value("Attendance", a.name, "custom_lien_in_calendrier", nom, update_modified=False)


def leave_application_on_cancel(doc, method=None):
    """Hook : les tâches calendrier de cette absence disparaissent avec elle."""
    for a in frappe.get_all("Attendance", filters={"leave_application": doc.name},
                            fields=["name", "custom_lien_in_calendrier"]):
        if a.custom_lien_in_calendrier and frappe.db.exists("Tache de travail", a.custom_lien_in_calendrier):
            frappe.delete_doc("Tache de travail", a.custom_lien_in_calendrier, ignore_permissions=True, force=True)
        if a.custom_lien_in_calendrier:
            frappe.db.set_value("Attendance", a.name, "custom_lien_in_calendrier", None, update_modified=False)
    # Trace de récupération : elle suit l'absence.
    for tr in frappe.get_all("Recuperation Acquise", filters={"leave_application": doc.name}, pluck="name"):
        frappe.delete_doc("Recuperation Acquise", tr, ignore_permissions=True, force=True)


def tache_quotidienne():
    """Scheduler : complète la planification (rien à faire tant qu'aucune règle n'est active)."""
    if not frappe.db.exists("Regle Recuperation", {"actif": 1}):
        return
    frappe.set_user("Administrator")
    planifier_recuperations()
    frappe.db.commit()
