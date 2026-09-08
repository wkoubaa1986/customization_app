from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields({"Sales Invoice": [{
        "fieldname": "custom_ristourne_commandes",
        "label": "Ristourne des commandes (suivi automatique)",
        "fieldtype": "Small Text",
        "insert_after": "discount_amount",
        "hidden": 1,
        "read_only": 1,
        "no_copy": 1,
    }]})
