"""Marge minimale de vente : champs de trace sur la ligne de commande (voir customization_app.marge_minimale).
Rejoué à chaque migration (after_migrate) : sans effet quand tout est en place."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app import marge_minimale as M


def execute():
    champs = [
        {"fieldname": M.CHAMP_FLAG, "label": "Prix relevé (marge mini)", "fieldtype": "Check",
         "insert_after": "rate", "read_only": 1, "no_copy": 1, "allow_on_submit": 0,
         "description": "Le prix de cette ligne a été relevé à la marge minimale (réglage « Config Marge Minimale »)."},
        {"fieldname": M.CHAMP_AVANT, "label": "Prix avant marge mini", "fieldtype": "Currency",
         "insert_after": M.CHAMP_FLAG, "read_only": 1, "no_copy": 1, "options": "currency",
         "depends_on": "eval:doc.%s" % M.CHAMP_FLAG},
    ]
    create_custom_fields({"Sales Order Item": champs, "Quotation Item": champs}, ignore_validate=True, update=True)
    if frappe.db.exists("DocType", M.CONFIG) and not frappe.db.get_single_value(M.CONFIG, "taux"):
        frappe.db.set_single_value(M.CONFIG, {"taux": 15, "actif": 1, "base": M.BASE_PRIX,
                                              "source_cout": M.SOURCE_VALO, "ignorer_web": 1, "ignorer_prix_zero": 1})
    if frappe.db.exists("DocType", M.CONFIG):
        for champ, valeur in (("arrondi", M.ARRONDI_DEFAUT), ("prix_ht_exonere", 1)):
            if not frappe.db.exists("Singles", {"doctype": M.CONFIG, "field": champ}):
                frappe.db.set_single_value(M.CONFIG, champ, valeur)
    # Le navigateur garde le formulaire (et ses scripts doctype_js) en cache local tant que la date de modification
    # du DocType n'a pas bougé : un simple rechargement de page ne prend pas le nouveau script (constat du 28/10/2026,
    # prix pas convertis en HT alors que le dev les convertissait dans un navigateur neuf). On la fait bouger à
    # chaque migration : chaque poste refait une lecture du DocType, puis repart sur son cache.
    for dt in ("Sales Order", "Quotation"):
        frappe.db.set_value("DocType", dt, "modified", frappe.utils.now(), update_modified=False)
        frappe.clear_cache(doctype=dt)
    frappe.db.commit()
