"""« Mes congés et ma paie » — l'écran RH du technicien sur son téléphone (décision utilisateur 10/11/2026).

L'application RH d'HRMS (/hrms) montre six liens rapides figés dans son code, en anglais, et on n'en
revient pas à l'écran du technicien. Ici : une page du Desk, dans la barre de navigation de l'écran
simplifié, qui ne fait que ce qu'on lui demande — ses soldes, demander un congé, suivre ses demandes,
ouvrir ses fiches de paie. Tout est calculé par HRMS (soldes, règles d'une demande) : l'écran ne
réinvente rien, il appelle `get_leave_details` et crée une Leave Application ordinaire, que
l'approbateur de la fiche traite comme d'habitude.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

PAGE = "rh-technicien"
FORMAT_FICHE = "Salary Slip Standard"


def _mon_employe():
    emp = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"},
                              ["name", "employee_name", "leave_approver", "holiday_list"], as_dict=True)
    if not emp:
        frappe.throw(_("Aucune fiche employé active n'est liée à votre compte."), frappe.PermissionError)
    return emp


def soldes(employe: str, date=None) -> list[dict]:
    """Les soldes par type de congé, selon HRMS : alloués, pris, en attente, restants."""
    from hrms.hr.doctype.leave_application.leave_application import get_leave_details
    d = get_leave_details(employe, date or nowdate())
    out = []
    for type_, v in (d.get("leave_allocation") or {}).items():
        out.append({"type": type_, "alloues": flt(v.get("total_leaves"), 1), "pris": flt(v.get("leaves_taken"), 1),
                    "en_attente": flt(v.get("leaves_pending_approval"), 1), "restants": flt(v.get("remaining_leaves"), 1),
                    "sans_solde": 0})
    for type_ in d.get("lwps") or []:
        out.append({"type": type_, "alloues": None, "pris": None, "en_attente": None, "restants": None, "sans_solde": 1})
    return out


@frappe.whitelist()
def etat() -> dict:
    emp = _mon_employe()
    demandes = frappe.get_all("Leave Application", filters={"employee": emp.name, "docstatus": ["<", 2]},
                              fields=["name", "leave_type", "from_date", "to_date", "total_leave_days", "status", "docstatus",
                                      "description", "half_day"], order_by="from_date desc", limit_page_length=12)
    fiches = frappe.get_all("Salary Slip", filters={"employee": emp.name, "docstatus": 1},
                            fields=["name", "start_date", "end_date", "net_pay", "gross_pay"], order_by="start_date desc",
                            limit_page_length=12)
    fmt = FORMAT_FICHE if frappe.db.exists("Print Format", FORMAT_FICHE) else "Standard"
    for f in fiches:
        f["pdf"] = "/api/method/frappe.utils.print_format.download_pdf?doctype=Salary%%20Slip&name=%s&format=%s&no_letterhead=0" % (
            frappe.utils.quote(f["name"]), frappe.utils.quote(fmt))
    feries = []
    if emp.holiday_list:
        feries = frappe.get_all("Holiday", filters={"parent": emp.holiday_list, "holiday_date": [">=", nowdate()],
                                                    "weekly_off": 0}, fields=["holiday_date", "description"],
                                order_by="holiday_date", limit_page_length=5)
    return {"employe": emp.name, "nom": emp.employee_name,
            "approbateur": frappe.utils.get_fullname(emp.leave_approver) if emp.leave_approver else None,
            "soldes": soldes(emp.name),
            "demandes": [dict(d, annulable=(d.docstatus == 0 and d.status == "Open")) for d in demandes],
            "fiches": fiches, "feries": [{"date": str(f.holiday_date), "libelle": f.description} for f in feries]}


@frappe.whitelist(methods=["POST"])
def demander_conge(leave_type, from_date, to_date=None, half_day=0, half_day_date=None, motif=None) -> dict:
    """Une demande de congé ordinaire (statut Open), que l'approbateur de la fiche traite comme d'habitude."""
    emp = _mon_employe()
    if not leave_type:
        frappe.throw(_("Choisissez le type de congé."))
    from_date = getdate(from_date)
    to_date = getdate(to_date or from_date)
    if to_date < from_date:
        frappe.throw(_("La date de fin précède la date de début."))
    doc = frappe.get_doc({
        "doctype": "Leave Application", "employee": emp.name, "leave_type": leave_type,
        "from_date": from_date, "to_date": to_date, "half_day": cint(half_day),
        "half_day_date": half_day_date if cint(half_day) else None,
        "description": (motif or "").strip() or None, "status": "Open",
        "leave_approver": emp.leave_approver or None, "posting_date": nowdate(),
    })
    doc.insert()         # avec les droits du technicien (rôle Employee), les règles HRMS jouent
    return {"name": doc.name, "jours": flt(doc.total_leave_days, 1), "statut": doc.status}


@frappe.whitelist(methods=["POST"])
def annuler_demande(name) -> dict:
    emp = _mon_employe()
    doc = frappe.get_doc("Leave Application", name)
    if doc.employee != emp.name:
        frappe.throw(_("Cette demande n'est pas la vôtre."), frappe.PermissionError)
    if doc.docstatus != 0 or doc.status != "Open":
        frappe.throw(_("Cette demande est déjà traitée ({0}) : voyez avec {1}.").format(_(doc.status), doc.leave_approver or _("votre responsable")))
    # Le rôle Employee ne peut pas supprimer une demande ; la sienne, encore ouverte, oui (contrôlé ci-dessus).
    doc.delete(ignore_permissions=True)
    return {"name": name, "supprimee": True}
