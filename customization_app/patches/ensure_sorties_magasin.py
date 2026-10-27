"""
Sorties du Magasin (27/10/2026) — idempotent, rejoué à chaque migration (after_migrate) :
1. rôle « Magasinier principal », posé sur Hedi ibidhii et Wassim Koubaa (fiche User fait foi ensuite : le patch
   n'ajoute que s'il manque, ne retire jamais) ;
2. champs de vérification sur Delivery Note (sortie vérification / sorti par / le / vérifié par / le / note) ;
3. workflow « Validation Magasin » (il vit en base) : Approve et Reject réservés au rôle, nouvelle action
   « Sortie exceptionnelle » (En attente → Approved) ouverte à tous SAUF aux magasiniers principaux (condition).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app import sorties_magasin as M

MODULE = "Customize erpnext"
CONDITION_EXCEPTIONNELLE = ('not frappe.db.get_value("Has Role", {"parent": frappe.session.user, "parenttype": "User", '
                            '"role": "Magasinier principal"}, "name")')


def execute():
    _role()
    _champs()
    _workflow()
    frappe.db.commit()


def _role():
    if not frappe.db.exists("Role", M.ROLE_PRINCIPAL):
        frappe.get_doc({"doctype": "Role", "role_name": M.ROLE_PRINCIPAL, "desk_access": 1}).insert(ignore_permissions=True)
    for user in M.PRINCIPAUX_DEFAUT:
        if frappe.db.exists("User", user) and not frappe.db.exists("Has Role", {"parent": user, "parenttype": "User",
                                                                                 "role": M.ROLE_PRINCIPAL}):
            frappe.get_doc("User", user).add_roles(M.ROLE_PRINCIPAL)


def _champs():
    create_custom_fields({"Delivery Note": [
        {"fieldname": M.CHAMP_VERIF, "label": "Sortie : vérification magasin", "fieldtype": "Select",
         "options": "\nÀ vérifier\nConforme\nÉcart", "insert_after": M.CHAMP_FLAG, "read_only": 1,
         "allow_on_submit": 1, "in_standard_filter": 1, "module": MODULE,
         "description": "Sortie exceptionnelle (BL validé sans le magasinier principal) : à vérifier par lui."},
        {"fieldname": M.CHAMP_SORTI_PAR, "label": "Sorti par", "fieldtype": "Link", "options": "User",
         "insert_after": M.CHAMP_VERIF, "read_only": 1, "allow_on_submit": 1, "module": MODULE},
        {"fieldname": M.CHAMP_SORTI_LE, "label": "Sorti le", "fieldtype": "Datetime",
         "insert_after": M.CHAMP_SORTI_PAR, "read_only": 1, "allow_on_submit": 1, "module": MODULE},
        {"fieldname": M.CHAMP_VERIFIE_PAR, "label": "Vérifié par (magasin)", "fieldtype": "Link", "options": "User",
         "insert_after": M.CHAMP_SORTI_LE, "read_only": 1, "allow_on_submit": 1, "module": MODULE},
        {"fieldname": M.CHAMP_VERIFIE_LE, "label": "Vérifié le", "fieldtype": "Datetime",
         "insert_after": M.CHAMP_VERIFIE_PAR, "read_only": 1, "allow_on_submit": 1, "module": MODULE},
        {"fieldname": M.CHAMP_NOTE, "label": "Vérification : note / écart", "fieldtype": "Small Text",
         "insert_after": M.CHAMP_VERIFIE_LE, "read_only": 1, "allow_on_submit": 1, "module": MODULE},
    ]}, ignore_validate=True)


def _workflow():
    if not frappe.db.exists("Workflow", M.WORKFLOW):
        return
    if not frappe.db.exists("Workflow Action Master", M.ACTION_EXCEPTIONNELLE):
        frappe.get_doc({"doctype": "Workflow Action Master",
                        "workflow_action_name": M.ACTION_EXCEPTIONNELLE}).insert(ignore_permissions=True)
    wf = frappe.get_doc("Workflow", M.WORKFLOW)
    change = False
    for t in wf.transitions:
        if t.state == M.EN_ATTENTE and t.action in (M.ACTION_APPROUVER, M.ACTION_REJETER) and t.allowed != M.ROLE_PRINCIPAL:
            t.allowed = M.ROLE_PRINCIPAL
            change = True
    exc = next((t for t in wf.transitions if t.state == M.EN_ATTENTE and t.action == M.ACTION_EXCEPTIONNELLE), None)
    if not exc:
        wf.append("transitions", {"state": M.EN_ATTENTE, "action": M.ACTION_EXCEPTIONNELLE, "next_state": M.APPROUVE,
                                  "allowed": "All", "allow_self_approval": 1, "condition": CONDITION_EXCEPTIONNELLE})
        change = True
    elif exc.allowed != "All" or exc.condition != CONDITION_EXCEPTIONNELLE or exc.next_state != M.APPROUVE:
        exc.allowed, exc.condition, exc.next_state, exc.allow_self_approval = "All", CONDITION_EXCEPTIONNELLE, M.APPROUVE, 1
        change = True
    if change:
        wf.flags.ignore_permissions = True
        wf.save()
        print("[ensure_sorties_magasin] workflow « Validation Magasin » aligné.")
