"""Champs de qualité uniquement : aucun appel externe et aucune activation."""
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    champs = [
        ("custom_geocode_statut", "Qualité de la position", "Select", {"options": "\nFiable\nValidée\nÀ vérifier\nIntrouvable\nIndisponible"}),
        ("custom_geocode_motif", "Contrôle de localisation", "Small Text", {}),
        ("custom_geocode_expire_le", "Position à actualiser après", "Datetime", {}),
        ("custom_geocode_valide_par", "Position confirmée par", "Link", {"options": "User"}),
        ("custom_geocode_signature", "Empreinte de l’adresse localisée", "Data", {"hidden": 1}),
    ]
    precedent = "custom_geocode_source"
    definitions = []
    for nom, label, type_, extras in champs:
        definitions.append(dict(fieldname=nom, label=label, fieldtype=type_, insert_after=precedent,
                                read_only=1, no_copy=1, module="Customize erpnext", **extras))
        precedent = nom
    create_custom_fields({"Address": definitions}, ignore_validate=True)
