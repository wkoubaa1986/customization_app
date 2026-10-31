"""Sous-traitants (décision utilisateur 30/10/2026) : le rôle, les champs de l'employé, les droits.

- Rôle « Sous-traitant » (accès Desk) ;
- Employee : `custom_sous_traitant` (case), `custom_compte_caisse` (compte qui reçoit ses encaissements),
  `custom_couleur` (couleur du calendrier, facultative) ;
- droits du rôle : ses tâches (lecture/écriture, filtrées par hooks), commandes, bons de livraison,
  clients, articles, entrepôts, vérifications et clôtures de caisse en lecture.
Idempotent.
"""
from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

ROLE = "Sous-traitant"
DROITS = {
    "Tache de travail": ("read", "write"),
    "Sales Order": ("read",),
    "Delivery Note": ("read",),
    "Customer": ("read",),
    "Item": ("read",),
    "Item Price": ("read",),
    "Warehouse": ("read",),
    "Verification Stock": ("read",),
    "Cloture Caisse": ("read",),
    "Employee": ("read",),
}


def execute():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
    create_custom_fields({"Employee": [
        {"fieldname": "custom_sous_traitant", "label": "Sous-traitant", "fieldtype": "Check",
         "insert_after": "custom_warehouse",
         "description": "Intervenant externe : son stock, son compte de caisse, ses interventions seulement."},
        {"fieldname": "custom_compte_caisse", "label": "Compte de caisse (sous-traitant)", "fieldtype": "Link",
         "options": "Account", "insert_after": "custom_sous_traitant", "depends_on": "eval:doc.custom_sous_traitant"},
        {"fieldname": "custom_couleur", "label": "Couleur du calendrier", "fieldtype": "Color",
         "insert_after": "custom_compte_caisse", "depends_on": "eval:doc.custom_sous_traitant"},
    ], "Sales Order": [
        # Une remise faite à la clôture mobile se justifie : « motif — commentaire » (31/10/2026).
        {"fieldname": "custom_justification_remise", "label": "Justification de la remise", "fieldtype": "Small Text",
         "insert_after": "discount_amount", "read_only": 1, "depends_on": "eval:doc.custom_justification_remise"},
    ], "Payment Schedule": [
        # Une dette saisie à la clôture mobile se justifie : « motif — commentaire » (31/10/2026).
        {"fieldname": "custom_justification_dette", "label": "Justification de la dette", "fieldtype": "Small Text",
         "insert_after": "custom_banque", "read_only": 1, "depends_on": "eval:doc.custom_justification_dette"},
    ]}, ignore_validate=True, update=True)
    for doctype, types in DROITS.items():
        if not frappe.db.exists("DocType", doctype):
            continue
        if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": ROLE, "permlevel": 0}):
            add_permission(doctype, ROLE, 0)
        for t in types:
            update_permission_property(doctype, ROLE, 0, t, 1)
    frappe.clear_cache()
    frappe.db.commit()
