"""Réponses d'appel « Ne veut plus d'entretien » / « Rappeler plus tard » (+ date), qualification IA des réponses libres,
relance reportée et SMS de réveil annuel (15/10/2026). Idempotent."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

NOUVELLES_OPTIONS = ("Ne veut plus d'entretien", "Rappeler plus tard")


def execute():
    create_custom_fields({"Customer": [
        {"fieldname": "custom_relance_reportee_au", "label": "Relance reportée au", "fieldtype": "Date",
         "insert_after": "custom_statut_relance", "module": "Customize erpnext",
         "description": "« Rappeler plus tard » à l’appel (ou réponse libre qualifiée) : ni SMS ni appel jusqu’à cette date, "
                        "puis le client revient dans la liste d’appels du jour."},
        {"fieldname": "custom_sms_reveil_le", "label": "SMS de réveil envoyé le", "fieldtype": "Date", "read_only": 1,
         "insert_after": "custom_relance_reportee_au", "module": "Customize erpnext",
         "description": "Un SMS par an aux clients « À requalifier » sans contact depuis un an."},
    ]}, ignore_validate=True, update=True)
    frappe.clear_cache(doctype="Customer")

    # « Appelle Client » est un DocType personnalisé (custom = 1) : ses champs se modifient directement.
    champ = frappe.db.get_value("DocField", {"parent": "Appelle Client", "fieldname": "resume_appel"}, ["name", "options"], as_dict=True)
    if champ:
        options = [o for o in (champ.options or "").split("\n")]
        manquantes = [o for o in NOUVELLES_OPTIONS if o not in options]
        if manquantes:
            frappe.db.set_value("DocField", champ.name, "options", "\n".join(options).rstrip("\n") + "\n" + "\n".join(manquantes), update_modified=False)
    dt = frappe.get_doc("DocType", "Appelle Client")
    existants = {f.fieldname for f in dt.fields}
    ajoutes = False
    for f in (
        {"fieldname": "rappeler_le", "label": "Rappeler le", "fieldtype": "Date", "insert_after": "resume_appel",
         "depends_on": "eval:doc.resume_appel == 'Rappeler plus tard'", "description": "Vide = dans 3 mois."},
        {"fieldname": "qualification_ia", "label": "Qualification IA", "fieldtype": "Data", "read_only": 1,
         "insert_after": "reponse_client", "description": "Lecture automatique de la réponse libre (06:30)."},
    ):
        if f["fieldname"] in existants:
            continue
        apres = f.pop("insert_after")
        idx = next((i for i, x in enumerate(dt.fields) if x.fieldname == apres), len(dt.fields) - 1) + 1
        row = dt.append("fields", f)          # ajouté en fin, puis placé après `apres`
        dt.fields.remove(row)
        dt.fields.insert(idx, row)
        ajoutes = True
    if ajoutes:
        for i, x in enumerate(dt.fields, 1):
            x.idx = i
        dt.flags.ignore_permissions = True
        dt.save()
    frappe.clear_cache(doctype="Appelle Client")
    frappe.clear_cache(doctype="Liste Appelle Entretien")
    print("ensure_qualification_appels : options %s, champs %s" % ("ok", "ajoutés" if ajoutes else "déjà là"))
