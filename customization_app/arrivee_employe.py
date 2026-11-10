"""Arrivée d'un technicien (décision utilisateur 10/11/2026) — le miroir du départ (depart_employe.py).

Un nouveau technicien interne se créait à la main : utilisateur et rôles, fiche Employé, entrepôt
« Stock Prénom Nom » et sa vérification hebdomadaire, puis une ligne dans chaque réglage (portail de
rendez-vous, prise de rendez-vous ERPNext, suivi d'activité, primes, responsable réparation, caisse,
véhicule GPS, domicile pour les tournées), la règle de récupération… Un oubli, et il n'apparaît pas
dans le portail ou n'a pas de caisse.

UN BOUTON sur la liste des employés (« Nouveau technicien »), réservé à la direction :
  - l'utilisateur avec les rôles du PROFIL TECHNICIEN (`ROLES_TECHNICIEN` : ceux de Mohamed Hedi, plus
    « Technicien » qui ouvre l'agenda du téléphone) — Raven crée son compte de messagerie tout seul ;
  - la fiche Employé, son entrepôt, la vérification de stock planifiée et le seuil de réapprovisionnement ;
  - l'ÉCRAN SIMPLIFIÉ du téléphone (`custom_ecran_simplifie`) : le même que le sous-traitant — Ma journée,
    agenda, mon stock, ma caisse, clôture mobile — SANS ses règles d'argent (sa caisse et son stock restent
    ceux d'un employé : comptage quotidien, espèces de la société) ;
  - une ligne à son nom dans chaque réglage (`REGLAGES`) ; le véhicule GPS seulement s'il est donné ; le
    domicile (lien Google Maps) alimente les tournées par le hook existant (domicile_employe.py) ;
  - la date de départ de sa caisse (jour d'entrée) et, si un premier jour est donné, la règle de
    récupération par quinzaine ;
  - un journal sur sa fiche, la direction prévenue.

Les réglages se posent chacun dans leur propre point de reprise : un réglage qui refuse la ligne est
signalé dans le journal, les autres passent, la création ne tombe pas pour autant.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

ROLES = ("System Manager", "HR Manager", "Congés RH")
ROLE_TECHNICIEN = "Technicien"
#: le profil : les rôles de Mohamed Hedi Chouchane (relevés le 10/11/2026) + « Technicien »
ROLES_TECHNICIEN = ("Employee", "Maintenance Manager", "POS Manager", "Raven User", "Sales Manager", "Sales User",
                    "Stock User", ROLE_TECHNICIEN)
CHAMP_ECRAN = "custom_ecran_simplifie"
SEUIL_DEFAUT = 1.0
MODE_PRIME_DEFAUT = "Ventes"
#: (réglage unique, table enfant, valeurs par ligne — fonction (emp, user) → dict, ou None pour sauter)
REGLAGES = (
    ("Config Portail RDV", "Portail RDV Employe", lambda emp, user, p: {"employe": emp.name, "nom_employe": emp.employee_name}),
    ("Appointment Booking Settings", "Assignment Rule User", lambda emp, user, p: {"user": user} if user else None),
    ("Config Suivi Activite", "Config Suivi Activite Employe",
     lambda emp, user, p: {"employee": emp.name, "nom": emp.employee_name, "utilisateur": user, "responsable": 0}),
    ("Config Prime", "Config Prime Employe",
     lambda emp, user, p: {"employee": emp.name, "employee_name": emp.employee_name, "mode": MODE_PRIME_DEFAUT}),
    ("Config Reparation Osmoseur", "Config Reparation Responsable",
     lambda emp, user, p: {"employee": emp.name, "nom": emp.employee_name}),
    ("Config Caisse", "Config Caisse Depart",
     lambda emp, user, p: {"caisse": emp.employee_name, "date_depart": p["date_entree"]}),
    ("Config Flotte GPS", "Config Flotte GPS Vehicule",
     lambda emp, user, p: {"vehicule": p["vehicule"], "cbox": p["cbox"], "employe": emp.name,
                           "employe_nom": emp.employee_name, "actif": 1} if p.get("vehicule") and p.get("cbox") else None),
)


TYPE_CONGE_PAYE = "Congé (Vacances)"
JOURS_CONGE_PAR_MOIS = 1.5      # 18 jours par an (décision utilisateur, usage de l'entreprise)


# ── Logique pure ─────────────────────────────────────────────────────────────

def jours_conges_payes(date_entree) -> float:
    """Le droit à congé payé jusqu'au 31/12 de l'année d'entrée : 1,5 jour par mois restant, mois d'entrée
    compris, arrondi au demi-jour (Hedi Ibidhi entré en octobre : 4,5). PURE."""
    d = getdate(date_entree)
    mois = 12 - d.month + 1
    return round(mois * JOURS_CONGE_PAR_MOIS * 2) / 2


def decouper_nom(nom: str) -> tuple[str, str | None]:
    """« Prénom Nom Composé » → (prénom, reste). PURE."""
    nom = " ".join((nom or "").split())
    prenom, _sep, reste = nom.partition(" ")
    return prenom, (reste or None)


def ligne_existe(lignes: list[dict], valeurs: dict, cles: tuple[str, ...]) -> bool:
    """Une ligne de réglage qui porte déjà les mêmes valeurs sur `cles`. PURE."""
    return any(all((l.get(c) or "") == (valeurs.get(c) or "") for c in cles) for l in lignes)


def cles_identite(valeurs: dict) -> tuple[str, ...]:
    """Les champs qui identifient une ligne de réglage : le lien employé / utilisateur, ou le nom de caisse. PURE."""
    for c in ("employe", "employee", "user", "utilisateur", "caisse"):
        if c in valeurs:
            return (c,)
    return tuple(valeurs)


# ── Création ─────────────────────────────────────────────────────────────────

def _champ_table(single: str, enfant: str) -> str | None:
    meta = frappe.get_meta(single)
    return next((f.fieldname for f in meta.get_table_fields() if f.options == enfant), None)


def _ajouter_ligne(single: str, enfant: str, valeurs: dict) -> str:
    """Ajoute une ligne au réglage unique si elle n'y est pas. -> « ajouté » / « déjà là » / « sans table »."""
    if not frappe.db.exists("DocType", single) or not frappe.db.exists("DocType", enfant):
        return "sans table"
    champ = _champ_table(single, enfant)
    if not champ:
        return "sans table"
    doc = frappe.get_single(single)
    lignes = [r.as_dict() for r in (doc.get(champ) or [])]
    if ligne_existe(lignes, valeurs, cles_identite(valeurs)):
        return "déjà là"
    doc.append(champ, valeurs)
    doc.flags.ignore_permissions = True
    doc.save()
    return "ajouté"


def _poser_reglages(emp, user: str | None, params: dict, journal: list[str]):
    for single, enfant, fabrique in REGLAGES:
        valeurs = fabrique(emp, user, params)
        if not valeurs:
            continue
        point = "arrivee_%s" % frappe.generate_hash(length=6)
        frappe.db.savepoint(point)
        try:
            etat = _ajouter_ligne(single, enfant, valeurs)
            if etat == "ajouté":
                journal.append(_("ajouté à {0}").format(_(single)))
        except Exception as e:
            frappe.db.rollback(save_point=point)
            journal.append(_("⚠️ {0} a refusé la ligne : {1}").format(_(single), str(e)[:120]))


def _seuil_stock(entrepot: str, journal: list[str]):
    from customization_app import stock_entrepots as SE
    if not frappe.db.exists("DocType", SE.CONFIG):
        return
    cfg = frappe.get_single(SE.CONFIG)
    if any(r.entrepot == entrepot for r in (cfg.get("seuils") or [])):
        return
    cfg.append("seuils", {"entrepot": entrepot, "seuil": SEUIL_DEFAUT, "cible": SEUIL_DEFAUT})
    cfg.flags.ignore_permissions = True
    cfg.save()
    journal.append(_("seuil de réapprovisionnement {0} posé sur {1}").format(SEUIL_DEFAUT, entrepot))


def _permission_employe(user: str | None, employe: str) -> int:
    """La permission utilisateur « Employé = employe » pour `user`, si elle manque. -> 1 si créée."""
    if not user or not frappe.db.exists("User", user) or frappe.db.exists(
            "User Permission", {"user": user, "allow": "Employee", "for_value": employe}):
        return 0
    frappe.get_doc({"doctype": "User Permission", "user": user, "allow": "Employee", "for_value": employe,
                    "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
    return 1


def _assurer_role_technicien():
    if not frappe.db.exists("Role", ROLE_TECHNICIEN):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE_TECHNICIEN, "desk_access": 1}).insert(ignore_permissions=True)


def _direction() -> list[str]:
    from customization_app.depart_employe import _direction
    return _direction()


@frappe.whitelist()
def options_arrivee() -> dict:
    """Ce que le dialogue propose : les approbateurs de congés possibles et les employés actifs (calendrier)."""
    frappe.only_for(ROLES)
    approbateurs = sorted({r.parent for r in frappe.get_all("Has Role", filters={"role": "Leave Approver", "parenttype": "User"},
                                                            fields=["parent"])
                           if frappe.db.get_value("User", r.parent, "enabled") and not r.parent.startswith("agent-ia")
                           and r.parent != "Administrator"})
    return {"approbateurs": [{"value": u, "label": frappe.utils.get_fullname(u) or u} for u in approbateurs],
            "employes": [{"value": e.name, "label": e.employee_name}
                         for e in frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name"],
                                                 order_by="employee_name")]}


@frappe.whitelist(methods=["POST"])
def creer_technicien(nom, email, mot_de_passe, telephone=None, date_entree=None, genre="Male", date_naissance=None,
                     domicile_lien=None, vehicule=None, cbox=None, jour_verification="Lundi", heure_verification="09:00",
                     periodicite="Chaque semaine", premier_jour_recup=None, ecran_simplifie=1, leave_approver=None,
                     calendrier_visible_par=None, voit_calendrier_de=None, conges_payes=None) -> dict:
    """Tout en un geste : utilisateur (profil Technicien), employé, entrepôt + vérification + seuil, réglages,
    caisse, récupération, écran simplifié, approbateur de congés, visibilité des calendriers.
    `calendrier_visible_par` : les EMPLOYÉS (leurs utilisateurs) qui voient son calendrier ;
    `voit_calendrier_de` : les employés dont il voit le calendrier. Lui-même toujours.
    `conges_payes` : jours de congé payé jusqu'au 31/12 (None = 1,5 j/mois restant ; 0 = rien).
    Rend {employe, utilisateur, entrepot, journal}."""
    import json

    from customization_app import sous_traitant as ST

    frappe.only_for(ROLES)
    if isinstance(calendrier_visible_par, str):
        calendrier_visible_par = json.loads(calendrier_visible_par or "[]")
    if isinstance(voit_calendrier_de, str):
        voit_calendrier_de = json.loads(voit_calendrier_de or "[]")
    calendrier_visible_par = [e for e in (calendrier_visible_par or []) if e]
    voit_calendrier_de = [e for e in (voit_calendrier_de or []) if e]
    if leave_approver and "Leave Approver" not in frappe.get_roles(leave_approver):
        frappe.throw(_("{0} n'a pas le rôle « Leave Approver » : il ne peut pas approuver les congés.").format(leave_approver))
    nom = " ".join((nom or "").split())
    email = (email or "").strip().lower()
    if not nom or not email or not mot_de_passe:
        frappe.throw(_("Nom, e-mail et mot de passe sont obligatoires."))
    if frappe.db.exists("User", email):
        frappe.throw(_("L'utilisateur {0} existe déjà.").format(email))
    if frappe.db.exists("Employee", {"employee_name": nom, "status": "Active"}):
        frappe.throw(_("Un employé actif s'appelle déjà {0}.").format(nom))
    date_entree = getdate(date_entree or nowdate())
    params = {"date_entree": str(date_entree), "vehicule": (vehicule or "").strip(), "cbox": (cbox or "").strip()}
    journal: list[str] = []

    _assurer_role_technicien()
    prenom, reste = decouper_nom(nom)
    user = frappe.get_doc({"doctype": "User", "email": email, "first_name": prenom, "last_name": reste,
                           "send_welcome_email": 0, "enabled": 1, "user_type": "System User",
                           "new_password": mot_de_passe, "mobile_no": telephone or None})
    user.flags.ignore_permissions = True
    user.insert()
    user.add_roles(*ROLES_TECHNICIEN)
    journal.append(_("utilisateur {0} créé avec le profil Technicien ({1} rôles)").format(email, len(ROLES_TECHNICIEN)))

    entrepot = ST._nouvel_entrepot(nom)
    valeurs = {
        "doctype": "Employee", "first_name": prenom, "last_name": reste, "employee_name": nom,
        "gender": genre or "Male", "date_of_birth": date_naissance or "1990-01-01", "date_of_joining": date_entree,
        "company": ST._societe(), "status": "Active", "user_id": email, "cell_number": telephone or None,
        "custom_warehouse": entrepot, "create_user_permission": 1,
    }
    if frappe.db.has_column("Employee", CHAMP_ECRAN):
        valeurs[CHAMP_ECRAN] = cint(ecran_simplifie)
    if domicile_lien and frappe.db.has_column("Employee", "custom_domicile_lien"):
        valeurs["custom_domicile_lien"] = domicile_lien.strip()     # → point de départ des tournées (hook domicile)
    if leave_approver and frappe.get_meta("Employee").has_field("leave_approver"):
        valeurs["leave_approver"] = leave_approver
    # Jours fériés (application RH du téléphone, présences) : la liste de la société.
    liste_feries = frappe.db.get_value("Company", valeurs["company"], "default_holiday_list")
    if liste_feries:
        valeurs["holiday_list"] = liste_feries
    emp = frappe.get_doc(valeurs)
    emp.flags.ignore_permissions = True
    emp.insert()
    journal.append(_("fiche {0} créée, entrepôt {1}").format(emp.name, entrepot))
    if domicile_lien:
        journal.append(_("domicile posé : point de départ des tournées"))
    if leave_approver:
        journal.append(_("approbateur de congés : {0}").format(frappe.utils.get_fullname(leave_approver)))
    # Qui voit le calendrier de qui = les « permissions utilisateur » sur Employé (mécanisme standard d'ERPNext,
    # tenu à la main depuis 2023 : chaque utilisateur porte la liste des employés dont il voit les documents —
    # tâches, présences, fiches de paie). La fiche crée déjà la sienne (`create_user_permission`).
    n = sum(_permission_employe(frappe.db.get_value("Employee", e, "user_id"), emp.name) for e in calendrier_visible_par)
    if n:
        journal.append(_("son calendrier est visible par {0} employé(s)").format(n))
    m = sum(_permission_employe(email, e) for e in voit_calendrier_de if e != emp.name)
    if m:
        journal.append(_("il voit le calendrier de {0} employé(s)").format(m))
    if cint(ecran_simplifie):
        journal.append(_("écran simplifié du téléphone activé"))

    ST._inscrire_verification(entrepot, jour_verification or "Lundi", heure_verification or "09:00",
                              periodicite or "Chaque semaine")
    journal.append(_("vérification du stock planifiée : {0}, {1}").format(jour_verification or "Lundi",
                                                                            periodicite or "Chaque semaine"))
    _seuil_stock(entrepot, journal)
    _poser_reglages(emp, email, params, journal)

    jours = jours_conges_payes(date_entree) if conges_payes in (None, "") else flt(conges_payes)
    if jours > 0 and frappe.db.exists("Leave Type", TYPE_CONGE_PAYE):
        alloc = frappe.get_doc({"doctype": "Leave Allocation", "employee": emp.name, "leave_type": TYPE_CONGE_PAYE,
                                "from_date": date_entree, "to_date": "%s-12-31" % date_entree.year,
                                "new_leaves_allocated": jours})
        alloc.flags.ignore_permissions = True
        alloc.insert()
        alloc.submit()
        journal.append(_("{0} jour(s) de {1} attribués jusqu'au 31/12/{2}").format(jours, TYPE_CONGE_PAYE, date_entree.year))

    if premier_jour_recup:
        from customization_app import conges_recuperations as CR
        try:
            CR.enregistrer_regle(emp.name, str(getdate(premier_jour_recup)), periodicite_semaines=2, horizon_semaines=8,
                                 actif=1, planifier=1)
            journal.append(_("règle de récupération par quinzaine à partir du {0}").format(
                frappe.utils.formatdate(premier_jour_recup)))
        except Exception as e:
            journal.append(_("⚠️ règle de récupération non posée : {0}").format(str(e)[:120]))

    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Employee",
                    "reference_name": emp.name,
                    "content": _("🆕 Arrivée traitée par {0} :<br>{1}").format(
                        frappe.utils.get_fullname(frappe.session.user),
                        "<br>".join(frappe.utils.escape_html(j) for j in journal))}).insert(ignore_permissions=True)
    for u in _direction():
        if u == frappe.session.user:
            continue
        try:
            frappe.get_doc({"doctype": "Notification Log", "for_user": u, "type": "Alert",
                            "subject": _("🆕 Nouveau technicien : {0}").format(nom),
                            "document_type": "Employee", "document_name": emp.name}).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "arrivee_employe: notification")
    frappe.db.commit()
    return {"employe": emp.name, "utilisateur": email, "entrepot": entrepot, "journal": journal}
