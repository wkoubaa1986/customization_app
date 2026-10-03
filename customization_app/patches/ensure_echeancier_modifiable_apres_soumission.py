"""Échéanciers de maintenance : la prolongation ajoute des visites à un échéancier SOUMIS, et le décalage écrit
completion_status / custom_sales_order sur des visites existantes. Frappe refuse tout cela tant que la table `schedules`
et ces champs ne sont pas « allow_on_submit » (UpdateAfterSubmitError — constaté le 03/10/2026, c'est ce qui aurait fait
planter la prolongation dès la correction du nom de champ). Property Setters, comme ceux déjà posés sur actual_date et
scheduled_date. Idempotent."""
import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
    make_property_setter("Maintenance Schedule", "schedules", "allow_on_submit", 1, "Check", validate_fields_for_doctype=False)
    for champ in ("scheduled_date", "actual_date", "completion_status", "item_code", "item_name", "sales_person"):
        make_property_setter("Maintenance Schedule Detail", champ, "allow_on_submit", 1, "Check", validate_fields_for_doctype=False)
    for champ in ("custom_sales_order", "custom_sms_1", "custom_sms_2", "custom_sms1_status", "custom_sms2_status", "custom_appelle", "custom_1er_appel"):
        if frappe.db.exists("Custom Field", {"dt": "Maintenance Schedule Detail", "fieldname": champ}):
            frappe.db.set_value("Custom Field", {"dt": "Maintenance Schedule Detail", "fieldname": champ}, "allow_on_submit", 1)
    frappe.clear_cache(doctype="Maintenance Schedule")
    frappe.clear_cache(doctype="Maintenance Schedule Detail")
