"""Fiches de paie mensuelles (demande du 24/11/2026) — HRMS fournit la fiche (Salary Slip), l'accès des employés
dans l'application RH (/hrms, PDF) ; les règles tunisiennes sont dans `paie_calcul` (pur, testé sur les bulletins
d'octobre 2026 du comptable).

- Fiche Employé, onglet « Paie » (champs réservés au rôle « Responsable paie ») : contrat (CDI / CIVP / Gérant),
  date de début des fiches (vide = pas de fiche : un CIVP attend l'accord du bureau de l'emploi), salaire de base
  et sa date d'effet. Enregistrer ces champs crée l'affectation de salaire HRMS et lance la génération.
- Chaque jour : une fiche par mois et par employé, de la date de début (pas avant janvier 2026) au mois en cours.
  Elle reste en brouillon, modifiable, jusqu'à 4 jours avant la fin du mois, puis elle est validée toute seule et
  l'employé est prévenu. Les mois passés (historique) sont validés tout de suite.
- Confidentialité : chacun ne lit QUE sa fiche validée ; le rôle « Responsable paie » (lui et le gérant) voit tout.
  Les User Permission sur Employee (posées pour les tâches) ne donnent plus accès à la paie des collègues.
- Aucune écriture comptable : seulement les fiches. Paiement par virement.
"""
import frappe
from frappe import _
from frappe.utils import add_days, flt, get_first_day, getdate, nowdate

from customization_app import paie_calcul as P

ROLE = "Responsable paie"
# Ceux qui voient toutes les fiches (sa réponse du 24/11/2026 : « moi et Najib le gérant »).
RESPONSABLES = ["koubaawassim@gmail.com", "aquaworld.servicing@gmail.com"]
STRUCTURES = {P.CDI: "Paie CDI", P.CIVP: "Paie CIVP", P.GERANT: "Paie Gérant"}
PROFIL_DE_STRUCTURE = {v: k for k, v in STRUCTURES.items()}
# clé du calcul → (composant, abréviation, type, code imprimé)
COMPOSANTS = {
    "base": ("Salaire de base", "SBASE", "Earning", "SBASE"),
    "prime_presence": ("Prime de présence", "PPRES", "Earning", "001"),
    "prime_transport": ("Prime de transport", "PTRANS", "Earning", "002"),
    "cnss": ("Retenue CNSS", "CNSS", "Deduction", "CNSS"),
    "irpp": ("Impôt sur le revenu", "IRPP", "Deduction", "IRPP"),
    "css": ("Contribution sociale de solidarité", "CSS", "Deduction", "CSS"),
}
NOS_COMPOSANTS = {c[0] for c in COMPOSANTS.values()}
CHAMPS_SALAIRE = ("custom_paie_profil", "custom_paie_base", "custom_paie_base_depuis", "custom_paie_debut")
FORMAT = "Aqua World Fiche de paie"
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
        "décembre"]


def libelle_mois(d) -> str:
    d = getdate(d)
    return "%s %d" % (MOIS[d.month - 1], d.year)


def de_mois(d) -> str:
    """« de novembre 2026 », « d’octobre 2026 »."""
    m = libelle_mois(d)
    return ("d’" if m[0] in "aeiouy" else "de ") + m


# ── Droits ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def est_responsable(user=None) -> bool:
    user = user or frappe.session.user
    return user == "Administrator" or ROLE in frappe.get_roles(user)


def pqc_fiche(user=None):
    """Liste des fiches : les siennes, validées. Les droits « voir la fiche Employé d'un collègue » (User Permission,
    posés pour les tâches) ne comptent pas ici."""
    user = user or frappe.session.user
    if est_responsable(user):
        return ""
    return ("(`tabSalary Slip`.docstatus = 1 and `tabSalary Slip`.employee in "
            "(select name from `tabEmployee` where user_id = %s))" % frappe.db.escape(user))


def hp_fiche(doc, ptype=None, user=None):
    user = user or frappe.session.user
    if est_responsable(user):
        return True
    if ptype not in (None, "read", "print", "email", "export"):
        return False
    return bool(doc.docstatus == 1 and doc.employee
                and frappe.db.get_value("Employee", doc.employee, "user_id") == user)


def pqc_reserve(user=None):
    """Affectations de salaire, salaires additionnels, lots de paie : le responsable seulement."""
    return "" if est_responsable(user) else "1=0"


def hp_reserve(doc, ptype=None, user=None):
    return est_responsable(user)


# ── Fiche Employé ──────────────────────────────────────────────────────────────────────────────────────────────────

def employe_validate(doc, method=None):
    if not doc.get("custom_paie_profil"):
        return
    if doc.get("custom_paie_debut") and not doc.get("custom_paie_base_depuis"):
        doc.custom_paie_base_depuis = doc.custom_paie_debut
    if doc.get("custom_paie_debut") and doc.get("custom_paie_base_depuis") \
            and getdate(doc.custom_paie_base_depuis) < getdate(doc.custom_paie_debut):
        frappe.throw(_("Paie : le salaire ne peut pas s’appliquer avant la date de début des fiches de paie."))


def employe_on_update(doc, method=None):
    if not any(doc.has_value_changed(c) for c in CHAMPS_SALAIRE):
        return
    if doc.get("custom_paie_profil") and flt(doc.get("custom_paie_base")) > 0 and doc.get("custom_paie_base_depuis"):
        synchroniser_affectation(doc)
    if doc.get("custom_paie_profil") and doc.get("custom_paie_debut"):
        frappe.enqueue("customization_app.paie.generer", employe=doc.name, queue="short", enqueue_after_commit=True)


def date_affectation(emp) -> "date":
    """Le salaire s'applique à partir du début du mois de sa date d'effet (HRMS cherche l'affectation en vigueur au
    premier jour payé du mois), jamais avant l'embauche (règle HRMS)."""
    depuis = get_first_day(emp.custom_paie_base_depuis or emp.custom_paie_debut)
    return max(getdate(depuis), getdate(emp.date_of_joining)) if emp.date_of_joining else getdate(depuis)


def synchroniser_affectation(emp):
    """Une affectation HRMS par date d'effet : un changement de salaire à une nouvelle date en crée une (l'historique
    reste) ; une correction à la même date remplace la précédente."""
    structure = STRUCTURES[emp.custom_paie_profil]
    depuis = date_affectation(emp)
    exist = frappe.db.get_value("Salary Structure Assignment", {"employee": emp.name, "from_date": depuis, "docstatus": 1},
                                ["name", "salary_structure", "base"], as_dict=True)
    if exist and exist.salary_structure == structure and flt(exist.base, 3) == flt(emp.custom_paie_base, 3):
        return
    if exist:
        ancienne = frappe.get_doc("Salary Structure Assignment", exist.name)
        ancienne.flags.ignore_permissions = True
        ancienne.cancel()
    ssa = frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": emp.name, "salary_structure": structure,
                          "company": emp.company, "from_date": depuis, "base": flt(emp.custom_paie_base, 3)})
    ssa.flags.ignore_permissions = True
    ssa.insert()
    ssa.submit()


# ── Calcul de chaque fiche (doc_event validate, APRÈS le calcul HRMS) ──────────────────────────────────────────────

def _ligne(doc, table, cle, montant):
    nom, abbr, _type, _code = COMPOSANTS[cle]
    row = next((r for r in doc.get(table) if r.salary_component == nom and not r.get("additional_salary")), None)
    if row is None:
        row = doc.append(table, {"salary_component": nom, "abbr": abbr})
    row.amount = row.default_amount = flt(montant, 3)
    row.additional_amount = 0
    row.depends_on_payment_days = 0
    row.do_not_include_in_total = 0


def calculer_fiche(doc, method=None):
    profil = PROFIL_DE_STRUCTURE.get(doc.salary_structure)
    if not profil:
        return
    ssa = getattr(doc, "_salary_structure_assignment", None) or {}
    base = flt(ssa.get("base"))
    emp = frappe.db.get_value("Employee", doc.employee, ["custom_paie_debut", "date_of_joining", "relieving_date"],
                              as_dict=True) or {}
    debut, fin = getdate(doc.start_date), getdate(doc.end_date)
    jours = P.jours_payes(debut, fin, getdate(emp.get("custom_paie_debut")) if emp.get("custom_paie_debut") else None,
                          getdate(emp.date_of_joining) if emp.get("date_of_joining") else None,
                          getdate(emp.relieving_date) if emp.get("relieving_date") else None,
                          flt(doc.get("custom_jours_absence")))
    # Les lignes ajoutées à la main sur le brouillon (prime exceptionnelle, avance…) restent et comptent.
    autres_gains = sum(flt(r.amount) for r in doc.earnings if r.salary_component not in NOS_COMPOSANTS)
    autres_retenues = sum(flt(r.amount) for r in doc.deductions if r.salary_component not in NOS_COMPOSANTS)
    b = P.bulletin(profil, base, jours, autres_gains, autres_retenues)
    _ligne(doc, "earnings", "base", b["base"])
    if P.PROFILS[profil]["primes"]:
        _ligne(doc, "earnings", "prime_presence", b["prime_presence"])
        _ligne(doc, "earnings", "prime_transport", b["prime_transport"])
    for cle in ("cnss", "irpp", "css"):
        _ligne(doc, "deductions", cle, b[cle])
    doc.custom_paie_profil = profil
    doc.custom_jours_payes = b["jours"]
    doc.custom_salaire_imposable = b["imposable"]
    doc.gross_pay = b["brut"]
    doc.base_gross_pay = flt(b["brut"] * flt(doc.exchange_rate or 1), 3)
    doc.set_net_pay()
    doc.compute_year_to_date()
    doc.compute_month_to_date()
    doc.compute_component_wise_year_to_date()


# ── Génération et validation automatiques ──────────────────────────────────────────────────────────────────────────

def tache_quotidienne():
    generer()


def _structure_en_vigueur(employe, fin):
    return frappe.db.get_value("Salary Structure Assignment", {"employee": employe, "docstatus": 1, "from_date": ("<=", fin)},
                               "salary_structure", order_by="from_date desc")


def generer(employe=None, aujourd_hui=None) -> dict:
    """Crée les fiches manquantes, rafraîchit les brouillons, valide celles dont le moment est venu. Rend
    {employé: [mois validés]} ; prévient chaque employé une fois par passage."""
    aujourd_hui = getdate(aujourd_hui or nowdate())
    filtres = {"custom_paie_profil": ["is", "set"], "custom_paie_debut": ["is", "set"], "status": ["!=", "Inactive"]}
    if employe:
        filtres["name"] = employe
    valides = {}
    for e in frappe.get_all("Employee", filters=filtres, fields=["name", "employee_name", "company", "user_id",
                                                                  "custom_paie_debut", "relieving_date"]):
        for premier in P.mois_a_traiter(getdate(e.custom_paie_debut), aujourd_hui,
                                        getdate(e.relieving_date) if e.relieving_date else None):
            try:
                nom = _fiche_du_mois(e, premier, aujourd_hui)
                if nom:
                    valides.setdefault(e.name, []).append((premier, nom))
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(frappe.get_traceback(), "Paie : fiche de %s, %s" % (e.employee_name, libelle_mois(premier)))
    for emp, mois in valides.items():
        try:
            _prevenir(emp, mois)
            frappe.db.commit()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Paie : notification")
    return {k: [libelle_mois(m) for m, _n in v] for k, v in valides.items()}


def _fiche_du_mois(e, premier, aujourd_hui):
    """Rend le nom de la fiche si elle vient d'être validée."""
    fin = P.fin_du_mois(premier)
    structure = _structure_en_vigueur(e.name, fin)
    if not structure or structure not in PROFIL_DE_STRUCTURE:
        return None
    exist = frappe.db.get_value("Salary Slip", {"employee": e.name, "start_date": premier, "end_date": fin},
                                ["name", "docstatus", "salary_structure"], as_dict=True, order_by="creation desc")
    if exist and exist.docstatus in (1, 2):
        return None            # validée, ou annulée à la main : on n'y touche plus
    if exist and exist.salary_structure != structure:
        frappe.delete_doc("Salary Slip", exist.name, ignore_permissions=True, force=True)   # changement de contrat
        exist = None
    if exist:
        doc = frappe.get_doc("Salary Slip", exist.name)
    else:
        doc = frappe.get_doc({"doctype": "Salary Slip", "employee": e.name, "company": e.company,
                              "payroll_frequency": "Monthly", "start_date": premier, "end_date": fin, "posting_date": fin})
    doc.flags.ignore_permissions = True
    doc.save()                 # crée ou recalcule (salaire changé, absences saisies…)
    if P.a_valider(premier, aujourd_hui):
        doc.submit()
        return doc.name
    return None


def _prevenir(employe, mois):
    user = frappe.db.get_value("Employee", employe, "user_id")
    if not user or not frappe.db.get_value("User", user, "enabled"):
        return
    noms = [libelle_mois(m) for m, _n in mois]
    texte = (_("🧾 Votre fiche de paie {0} est disponible").format(de_mois(mois[0][0])) if len(noms) == 1 else
             _("🧾 Vos fiches de paie sont disponibles : {0} → {1}").format(noms[0], noms[-1]))
    dernier = mois[-1][1]
    frappe.get_doc({"doctype": "PWA Notification", "from_user": "Administrator", "to_user": user, "message": texte,
                    "reference_document_type": "Salary Slip", "reference_document_name": dernier}).insert(ignore_permissions=True)
    frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": texte,
                    "document_type": "Salary Slip", "document_name": dernier}).insert(ignore_permissions=True)


# ── Installation (after_migrate, idempotente) ──────────────────────────────────────────────────────────────────────

def installer():
    _role()
    _champs()
    _composants_et_structures()
    _droits()
    _format()
    frappe.db.set_single_value("Payroll Settings", "email_salary_slip_to_employee", 0)   # PDF par mail : non
    for societe in frappe.get_all("Company", filters={"custom_cnss_employeur": ["is", "not set"]}, pluck="name"):
        # N° employeur imprimé sur les bulletins du comptable ; modifiable sur la fiche Société.
        frappe.db.set_value("Company", societe, "custom_cnss_employeur", "00651087-23", update_modified=False)


def _role():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
    for user in RESPONSABLES:
        if frappe.db.exists("User", user) and ROLE not in frappe.get_roles(user):
            u = frappe.get_doc("User", user)
            u.flags.ignore_permissions = True
            u.add_roles(ROLE)


def _champs():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    p = {"permlevel": 1}
    create_custom_fields({
        "Employee": [
            dict(fieldname="custom_tab_paie", label="Paie", fieldtype="Tab Break", insert_after="old_parent", **p),   # juste avant « Connexions »
            dict(fieldname="custom_paie_profil", label="Contrat (fiche de paie)", fieldtype="Select",
                 options="\nCDI\nCIVP\nGérant", insert_after="custom_tab_paie", **p,
                 description="CDI : CNSS 9,68 % + IRPP + CSS. CIVP : aucune retenue. Gérant : IRPP + CSS, sans CNSS."),
            dict(fieldname="custom_paie_debut", label="Fiches de paie à partir du", fieldtype="Date",
                 insert_after="custom_paie_profil", **p,
                 description="Vide = aucune fiche (ex. CIVP en attente de l’accord du bureau de l’emploi). "
                             "Jamais avant janvier 2026."),
            dict(fieldname="custom_paie_base", label="Salaire de base mensuel (DT)", fieldtype="Currency",
                 insert_after="custom_paie_debut", **p),
            dict(fieldname="custom_paie_base_depuis", label="Ce salaire s’applique à partir du", fieldtype="Date",
                 insert_after="custom_paie_base", **p,
                 description="Augmentation : nouveau montant et nouvelle date (début du mois concerné) ; "
                             "les fiches déjà validées ne changent pas."),
            dict(fieldname="custom_col_paie", fieldtype="Column Break", insert_after="custom_paie_base_depuis", **p),
            dict(fieldname="custom_paie_matricule", label="Matricule (paie)", fieldtype="Data",
                 insert_after="custom_col_paie", **p),
            dict(fieldname="custom_paie_cnss", label="N° CNSS", fieldtype="Data", insert_after="custom_paie_matricule", **p),
            dict(fieldname="custom_paie_qualification", label="Qualification (imprimée)", fieldtype="Data",
                 insert_after="custom_paie_cnss", **p),
            dict(fieldname="custom_paie_enfants", label="Enfants à charge", fieldtype="Int",
                 insert_after="custom_paie_qualification", **p),
        ],
        "Salary Slip": [
            dict(fieldname="custom_jours_absence", label="Jours d’absence non payés", fieldtype="Float",
                 insert_after="payment_days", allow_on_submit=0,
                 description="Sur 26 jours. La fiche se recalcule à l’enregistrement."),
            dict(fieldname="custom_jours_payes", label="Jours payés (sur 26)", fieldtype="Float", read_only=1,
                 insert_after="custom_jours_absence"),
            dict(fieldname="custom_salaire_imposable", label="Salaire imposable", fieldtype="Currency", read_only=1,
                 options="currency", insert_after="gross_pay"),
            dict(fieldname="custom_paie_profil", label="Contrat", fieldtype="Data", read_only=1,
                 insert_after="custom_jours_payes"),
        ],
        "Company": [
            dict(fieldname="custom_cnss_employeur", label="N° CNSS employeur", fieldtype="Data",
                 insert_after="tax_id"),
        ],
    }, update=True)


# À ajouter à la main sur un brouillon (hors structures) : une prime compte dans le brut (CNSS, IRPP) ; une avance
# se retire du net, après impôt.
COMPOSANTS_LIBRES = [("Prime exceptionnelle", "PEXC", "Earning"), ("Avance sur salaire", "AVANCE", "Deduction")]


def _composants_et_structures():
    for nom, abbr, type_ in [c[:3] for c in COMPOSANTS.values()] + COMPOSANTS_LIBRES:
        if not frappe.db.exists("Salary Component", nom):
            frappe.get_doc({"doctype": "Salary Component", "salary_component": nom, "salary_component_abbr": abbr,
                            "type": type_, "depends_on_payment_days": 0, "remove_if_zero_valued": 0,
                            "is_tax_applicable": 0}).insert(ignore_permissions=True)
    societe = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name")[0]
    for profil, nom in STRUCTURES.items():
        if frappe.db.exists("Salary Structure", nom):
            continue
        regles = P.PROFILS[profil]
        gains = [{"salary_component": "Salaire de base", "abbr": "SBASE", "amount_based_on_formula": 1, "formula": "base"}]
        if regles["primes"]:
            gains += [{"salary_component": "Prime de présence", "abbr": "PPRES", "amount": P.PRIME_PRESENCE},
                      {"salary_component": "Prime de transport", "abbr": "PTRANS", "amount": P.PRIME_TRANSPORT}]
        retenues = [{"salary_component": c, "abbr": a, "amount": 0} for c, a in
                    (("Retenue CNSS", "CNSS"), ("Impôt sur le revenu", "IRPP"), ("Contribution sociale de solidarité", "CSS"))]
        s = frappe.get_doc({"doctype": "Salary Structure", "name": nom, "__newname": nom, "company": societe,
                            "payroll_frequency": "Monthly", "is_active": "Yes", "currency": "TND",
                            "earnings": gains, "deductions": retenues})
        s.flags.ignore_permissions = True
        s.insert()
        s.submit()


def _droits():
    from frappe.permissions import add_permission, update_permission_property
    for dt in ("Salary Slip", "Salary Structure Assignment", "Salary Structure", "Salary Component", "Additional Salary",
               "Payroll Entry"):
        if not frappe.db.exists("Custom DocPerm", {"parent": dt, "role": ROLE, "permlevel": 0}):
            add_permission(dt, ROLE, 0)
        soumettre = frappe.get_meta(dt).is_submittable
        for droit in ("read", "write", "create", "delete", "print", "report", "export", "email"):
            update_permission_property(dt, ROLE, 0, droit, 1, validate=False)
        if soumettre:
            for droit in ("submit", "cancel", "amend"):
                update_permission_property(dt, ROLE, 0, droit, 1, validate=False)
    # Les User Permission sur Employee (posées pour les tâches, « tous les types ») filtraient AUSSI la paie : le
    # gérant ne voyait que 33 fiches sur 46. Sur ces types, l'accès est réglé par pqc_* / hp_* ci-dessus (chacun sa
    # fiche, le responsable tout) : le filtre par employé n'a plus à s'appliquer.
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter
    for dt in ("Salary Slip", "Salary Structure Assignment", "Additional Salary"):
        make_property_setter(dt, "employee", "ignore_user_permissions", 1, "Check", validate_fields_for_doctype=False)
    # Les champs de l'onglet « Paie » de la fiche Employé (niveau 1) : le responsable seulement.
    if not frappe.db.exists("Custom DocPerm", {"parent": "Employee", "role": ROLE, "permlevel": 1}):
        add_permission("Employee", ROLE, 1)
    update_permission_property("Employee", ROLE, 1, "write", 1, validate=False)
    # Rapports de paie (registre des salaires…) : ils lisent les fiches sans passer par les droits ; réservés.
    for rapport in frappe.get_all("Report", filters={"module": "Payroll"}, pluck="name"):
        nom = frappe.db.get_value("Custom Role", {"report": rapport})
        cr = frappe.get_doc("Custom Role", nom) if nom else frappe.get_doc({"doctype": "Custom Role", "report": rapport})
        cr.set("roles", [{"role": ROLE}])
        cr.flags.ignore_permissions = True
        cr.save()
    frappe.clear_cache()


def _format():
    html = '{% include "templates/print/fiche_de_paie.html" %}'
    if frappe.db.exists("Print Format", FORMAT):
        pf = frappe.get_doc("Print Format", FORMAT)
    else:
        pf = frappe.new_doc("Print Format")
        pf.name = FORMAT
    pf.update({"doc_type": "Salary Slip", "module": "Customize erpnext", "standard": "No", "custom_format": 1,
               "print_format_type": "Jinja", "html": html, "disabled": 0})
    pf.flags.ignore_permissions = True
    pf.save()
    # Format par défaut : c'est lui que télécharge l'application RH (hrms.api.download_salary_slip).
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter
    make_property_setter("Salary Slip", None, "default_print_format", FORMAT, "Data", for_doctype=True,
                         validate_fields_for_doctype=False)
