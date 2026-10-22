"""
Suivi d'une commande en un clic (demande 22/10/2026) : « Ne répond pas », « À rappeler le… »,
« En instance jusqu'au… », posés depuis l'écran Commandes à traiter.

Salma suivait les commandes en texte libre (109 commentaires en octobre : « le client ne répond
pas » ×63, « en instance » ×26, « annuler la commande » ×11, « rappel après-midi »…) : aucune date
de reprise, rien qui fasse revenir la commande au bon moment. Le statut et la date de reprise
vivent désormais sur la commande ; tant que la date n'est pas atteinte, la commande sort du reste
à faire. Champs modifiables après validation, jamais copiés. Idempotent.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Customize erpnext"

CHAMPS = [
    {"fieldname": "custom_suivi", "label": "Suivi", "fieldtype": "Select",
     "options": "\nNe répond pas\nÀ rappeler\nEn instance", "insert_after": "custom_commentaire_par",
     "in_standard_filter": 1, "read_only": 1, "allow_on_submit": 1, "no_copy": 1, "module": MODULE,
     "description": "Posé depuis l'écran Commandes à traiter : la commande revient dans le reste à faire "
                    "à la date de reprise."},
    {"fieldname": "custom_suivi_le", "label": "Reprendre le", "fieldtype": "Datetime",
     "insert_after": "custom_suivi", "read_only": 1, "allow_on_submit": 1, "no_copy": 1, "module": MODULE},
    {"fieldname": "custom_suivi_note", "label": "Note de suivi", "fieldtype": "Data",
     "insert_after": "custom_suivi_le", "read_only": 1, "allow_on_submit": 1, "no_copy": 1, "module": MODULE},
    {"fieldname": "custom_suivi_par", "label": "Suivi posé par", "fieldtype": "Data",
     "insert_after": "custom_suivi_note", "read_only": 1, "allow_on_submit": 1, "no_copy": 1, "module": MODULE},
]


def execute():
    create_custom_fields({"Sales Order": CHAMPS}, ignore_validate=True)
