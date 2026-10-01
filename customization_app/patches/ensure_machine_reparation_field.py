"""Champ « Dossier machine » sur la Tache de travail.

Lie une tâche de réparation à un dossier « Machine Reparation » (atelier
osmoseurs). C'est ce lien que suit le hook on_update : la clôture de la dernière
tâche liée fait passer le dossier à « Réparée ».

Patch et non fixture : le champ est créé une fois, idempotent (create_custom_fields
ne recrée pas un champ existant), et ne dépend pas de l'ordre d'import des fixtures.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Machine Reparation"):
        return
    create_custom_fields(
        {
            "Tache de travail": [
                {
                    "fieldname": "machine_reparation",
                    "fieldtype": "Link",
                    "label": "Dossier machine (atelier)",
                    "options": "Machine Reparation",
                    "insert_after": "commande_client",
                    "read_only": 1,
                    "depends_on": "eval:doc.machine_reparation",
                    "module": "Customize erpnext",
                    "description": "Dossier de réparation d'osmoseur déposé à l'atelier (écran Réparation osmoseurs).",
                }
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
