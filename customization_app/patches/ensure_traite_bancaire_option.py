"""
Option « Traite bancaire » sur le champ `type` des deux tables de l'Outil
d'encaissement — « Liste des Dettes client » (paiements saisis) et
« Liste Dettes » (allocation sur les dettes).

Ces DocTypes sont CUSTOM (créés en base, pas dans une app) : l'option ne peut
pas être livrée par un fichier de DocType, seul un patch la porte en prod.
Idempotent : ne touche rien si l'option est déjà là.
"""

import frappe

OPTION = "Traite bancaire"
DOCTYPES = ("Liste des Dettes client", "Liste Dettes")


def execute():
    for doctype in DOCTYPES:
        if not frappe.db.exists("DocType", doctype):
            continue
        row = frappe.db.get_value(
            "DocField", {"parent": doctype, "fieldname": "type"},
            ["name", "options"], as_dict=True)
        if not row:
            continue
        options = (row.options or "").split("\n")
        if OPTION in options:
            continue
        frappe.db.set_value("DocField", row.name, "options",
                            "\n".join(options + [OPTION]), update_modified=False)
        frappe.clear_cache(doctype=doctype)
    frappe.db.commit()
